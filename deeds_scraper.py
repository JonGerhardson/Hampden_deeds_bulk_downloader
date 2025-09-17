import os
import sys
import re
import csv
import argparse
import asyncio
import urllib.parse
from pathlib import Path
from PIL import Image
import requests
import ocrmypdf
from playwright.async_api import async_playwright, BrowserContext, Locator, TimeoutError

# --- CONFIGURATION ---
# Main directory where all final output will be saved.
OUTPUT_DIR = Path("final_output")
# Directory for temporary files that are deleted after each run.
TEMP_DIR = Path("temp_downloads")

# --- SELECTORS (for the scraper) ---
ROW_SELECTOR = "//tr[.//a[@title='View Document Image']]"
DOC_LINK_SELECTOR = 'a[title="View Document Image"]'
DOC_ID_SELECTOR = "./td[7]"
NEXT_BUTTON_SELECTOR = 'a.nextPage'

# --- URL Generation Configuration ---
BASE_URL_FOR_GENERATION = "https://search.hampdendeeds.com/ALIS/WW400R.HTM"
STATIC_URL_PARAMS = {
    'W9ABR': 'TT', 'W9TOWN': 'CHIC', 'W9FDTA': '01012024',
    'W9TDTA': '', 'WSHTNM': 'WW414R00', 'WSIQTP': 'SY14AP',
    'WSKYCD': 'T', 'WSWVER': '2'
}

# --- SCRIPT FUNCTIONS ---

async def download_document(context: BrowserContext, doc_link: Locator, doc_id_text: str, download_dir: Path):
    """
    Handles opening the document page, determining the file type (TIF/PDF),
    and downloading the file to a specified directory.
    """
    filename_base = re.sub(r'[\\/*?:"<>|]', "_", doc_id_text.strip())
    if not filename_base:
        print("  ⚠️ Skipping download due to empty document ID.")
        return

    page = doc_link.page
    doc_page = None
    print(f"  -> Processing doc '{filename_base}'...")

    try:
        async with page.expect_popup(timeout=30000) as popup_info:
            await doc_link.click()

        doc_page = await popup_info.value
        await doc_page.wait_for_load_state("networkidle", timeout=30000)

        download_link = doc_page.locator('a:has-text("Download")').first
        await download_link.wait_for(state="visible", timeout=10000)
        image_url = await download_link.get_attribute('href', timeout=10000)

        if not image_url:
            raise Exception("Could not find download URL on popup page.")

        cookies = {c['name']: c['value'] for c in await context.cookies()}
        user_agent = await page.evaluate("() => navigator.userAgent")
        headers = {'User-Agent': user_agent, 'Referer': doc_page.url}

        response = requests.get(image_url, headers=headers, cookies=cookies, timeout=45)
        response.raise_for_status()

        content_type = response.headers.get('Content-Type', '').lower()
        if 'pdf' in content_type:
            file_extension = ".pdf"
        else:
            file_extension = ".tif" # Default to TIF for tiff images or generic streams

        output_filename = f"{filename_base}{file_extension}"
        output_path = download_dir / output_filename
        with open(output_path, 'wb') as f:
            f.write(response.content)

        print(f"  ✅ Successfully saved: {output_path}")

    except Exception as e:
        error_message = str(e).splitlines()[0]
        print(f"  ❌ Failed to download '{filename_base}'. Reason: {error_message}")
    finally:
        if doc_page and not doc_page.is_closed():
            await doc_page.close()

async def scrape_url(start_url: str, context: BrowserContext, download_dir: Path):
    """
    Takes a single starting URL, scrapes all documents across all paginated pages,
    and saves them to the specified download directory.
    """
    page = await context.new_page()
    print(f"\nNavigating to initial URL: {start_url}")
    await page.goto(start_url, wait_until="domcontentloaded")

    page_count = 1
    while True:
        print("-" * 60)
        print(f"▶️ Processing Page {page_count}...")
        
        # UPDATED: Explicitly check for the "no more results" message.
        no_more_results_locator = page.locator('text="Sorry, no (more) matching names found"')
        if await no_more_results_locator.is_visible():
            print("Found 'no more results' message. Ending scrape for this URL.")
            break
        
        try:
            # Wait for the results table to ensure the page has results.
            table_body_selector = "//tr[.//a[@title='View Document Image']]/.."
            await page.locator(table_body_selector).first.wait_for(state="visible", timeout=20000)
        except TimeoutError:
            print("Could not find the search results table. Ending scrape for this URL.")
            break

        rows = await page.locator(ROW_SELECTOR).all()
        print(f"Found {len(rows)} document rows on this page.")
        if not rows:
            # This is a fallback, but the message check above should catch this.
            break

        for i, row in enumerate(rows):
            try:
                doc_link = row.locator(DOC_LINK_SELECTOR).first
                doc_id_element = row.locator(f"xpath={DOC_ID_SELECTOR}")
                doc_id_text = await doc_id_element.inner_text(timeout=10000)
                await download_document(context, doc_link, doc_id_text, download_dir)
            except Exception as e:
                print(f"  ⚠️ Could not process row {i+1}. Error: {str(e).splitlines()[0]}")
        
        try:
            next_button = page.locator(NEXT_BUTTON_SELECTOR).first
            if not await next_button.is_visible():
                print("No visible 'Next' button. Assuming last page.")
                break
            await next_button.click()
            await page.wait_for_load_state("domcontentloaded", timeout=30000)
            page_count += 1
        except (TimeoutError, AttributeError):
            print("No 'Next' button found. Assuming last page.")
            break
    await page.close()


def process_downloads(source_dir: Path, output_pdf_path: Path):
    """
    Finds all TIF files in a directory, combines them, runs OCR,
    and saves the final searchable PDF.
    """
    print("-" * 60)
    print(f"▶️ Starting PDF processing for files in '{source_dir}'")

    tif_files = sorted([f for f in source_dir.iterdir() if f.suffix.lower() == '.tif'])

    if not tif_files:
        print("❌ No .TIF files found in the download directory to process.")
        return

    print(f"Found {len(tif_files)} TIF files to combine into '{output_pdf_path.name}'.")

    # Use a temporary PDF for the initial combination
    temp_pdf_path = source_dir / "temp_image_only.pdf"

    try:
        # Step 1: Combine TIFs into an image-only PDF
        img1 = Image.open(tif_files[0])
        other_images = [Image.open(f).convert('RGB') for f in tif_files[1:]]
        
        img1.convert('RGB').save(
            temp_pdf_path, "PDF", resolution=100.0, save_all=True, append_images=other_images
        )
        print("✅ Successfully created temporary combined PDF.")

        # Step 2: Perform OCR
        print("⏳ Performing OCR (this may take a while)...")
        ocrmypdf.api.ocr(
            input_file=temp_pdf_path,
            output_file=output_pdf_path,
            deskew=True,
            force_ocr=True,
            progress_bar=True
        )
        print(f"✅ Successfully created searchable PDF: '{output_pdf_path}'!")

    except ocrmypdf.exceptions.TesseractNotFoundError:
        print("\n❌ OCR Error: Tesseract is not installed or not in your system's PATH.")
        sys.exit(1)
    except Exception as e:
        print(f"\n❌ An error occurred during PDF processing: {e}")
    finally:
        # Step 3: Clean up temporary file
        if temp_pdf_path.exists():
            os.remove(temp_pdf_path)
            print("✅ Cleaned up temporary file.")


def generate_urls_in_csv(csv_filename: str):
    """
    Reads a CSV, generates search URLs for each 'Property Address',
    and overwrites the file with a new 'search_registry_url' column.
    """
    print(f"--- URL Generation Mode: Enriching '{csv_filename}' ---")
    try:
        with open(csv_filename, mode='r', newline='', encoding='utf-8') as f:
            reader = csv.reader(f)
            header = next(reader)
            address_idx = [h.lower() for h in header].index('property address')
            rows = list(reader)
    except (FileNotFoundError, ValueError, StopIteration) as e:
        print(f"Error reading CSV: {e}")
        return

    updated_rows = []
    for row in rows:
        if len(row) > address_idx and (address := row[address_idx].strip()):
            params = STATIC_URL_PARAMS.copy()
            params['W9PADR'] = address.upper()
            query_string = urllib.parse.urlencode(params)
            full_url = f"{BASE_URL_FOR_GENERATION}?{query_string}#schTerms"
            updated_rows.append(row + [full_url])
        else:
            updated_rows.append(row + [''])
    
    try:
        with open(csv_filename, mode='w', newline='', encoding='utf-8') as f:
            writer = csv.writer(f)
            writer.writerow(header + ['search_registry_url'])
            writer.writerows(updated_rows)
        print(f"✅ Success! '{csv_filename}' has been updated with URLs.")
    except IOError as e:
        print(f"Error writing to CSV: {e}")


async def main():
    """Main function to parse arguments and orchestrate the workflow."""
    parser = argparse.ArgumentParser(
        description="A tool to scrape documents from a deeds registry, combine, and OCR them.",
        formatter_class=argparse.RawTextHelpFormatter
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('url', nargs='?', default=None, help="A single starting URL to scrape.")
    group.add_argument('-i', '--input-file', help="Path to a CSV file containing a 'URL' column.")
    parser.add_argument('--generate-urls', action='store_true', help="Pre-processing step: Reads a CSV with 'Property Address' and adds search URLs. Must be used with -i.")
    
    args = parser.parse_args()

    if args.generate_urls:
        if not args.input_file:
            print("Error: --generate-urls requires the -i <filename.csv> argument.")
            sys.exit(1)
        generate_urls_in_csv(args.input_file)
        return

    urls_to_process = []
    if args.input_file:
        try:
            with open(args.input_file, mode='r', newline='', encoding='utf-8') as f:
                reader = csv.DictReader(f)
                urls_to_process = [row['URL'] for row in reader if row.get('URL')]
        except (FileNotFoundError, KeyError) as e:
            print(f"Error processing CSV file: {e}")
            sys.exit(1)
    elif args.url:
        urls_to_process.append(args.url)

    if not urls_to_process:
        print("No valid URLs found to process.")
        return

    OUTPUT_DIR.mkdir(exist_ok=True)
    TEMP_DIR.mkdir(exist_ok=True)

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False, slow_mo=50)
        context = await browser.new_context(accept_downloads=True)

        for i, start_url in enumerate(urls_to_process):
            run_id = i + 1
            print("=" * 70)
            print(f"🚀 STARTING RUN {run_id}/{len(urls_to_process)}")
            
            # Create a unique temporary subdirectory for this run
            run_temp_dir = TEMP_DIR / f"run_{run_id}"
            run_temp_dir.mkdir(exist_ok=True)
            
            # --- Step 1: Scrape all documents for this URL ---
            await scrape_url(start_url, context, run_temp_dir)

            # --- Step 2: Process the downloaded TIFs ---
            output_pdf_name = OUTPUT_DIR / f"document_set_{run_id}_OCR.pdf"
            process_downloads(run_temp_dir, output_pdf_name)

            # --- Step 3: Move TIFs to final output directory and clean up ---
            final_tifs_dir = OUTPUT_DIR / f"document_set_{run_id}_TIFs"
            final_tifs_dir.mkdir(exist_ok=True)
            for item in run_temp_dir.iterdir():
                if item.suffix.lower() in ['.tif', '.pdf']:
                    item.rename(final_tifs_dir / item.name)
            
            # Clean up the temporary run directory
            try:
                os.rmdir(run_temp_dir)
            except OSError:
                print(f"Warning: Could not remove temp directory {run_temp_dir}. It may contain non-TIF/PDF files.")


        await browser.close()

    # Final cleanup of the main temp directory
    try:
        os.rmdir(TEMP_DIR)
    except OSError:
        pass # It might not be empty if a run failed weirdly

    print("=" * 70)
    print("🎉 All tasks complete. Check the 'final_output' directory.")


if __name__ == "__main__":
    asyncio.run(main())



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

# --- COUNTY CONFIGURATION ---
COUNTY_CONFIG = {
    'hampden': {
        'name': 'Hampden County',
        'system': 'ALIS',
        'base_url': 'https://search.hampdendeeds.com/ALIS/WW400R.HTM',
        'search_type': 'url_params',
    },
    'hampshire': {
        'name': 'Hampshire County', 
        'system': 'perfect_vision',
        'base_url': 'https://www.masslandrecords.com/Hampshire/',
        'search_type': 'postback',
        'field_ids': {
            'last_name': 'SearchFormEx1_ACSTextBox_LastName1',
            'first_name': 'SearchFormEx1_ACSTextBox_FirstName1',
            'search_btn': 'SearchFormEx1_btnSearch',
            'reset_btn': 'SearchFormEx1_BtnReset',
        },
        'result_limit': 1000,
    }
}

# --- SELECTORS (for the scraper) ---
ROW_SELECTOR = "//tr[.//a[@title='View Document Image']]"
DOC_LINK_SELECTOR = 'a[title="View Document Image"]'
DOC_ID_SELECTOR = "./td[7]"
NEXT_BUTTON_SELECTOR = 'a.nextPage'

# --- Hampden URL Generation Configuration ---
BASE_URL_FOR_GENERATION = "https://search.hampdendeeds.com/ALIS/WW400R.HTM"
STATIC_URL_PARAMS = {
    'W9ABR': '*ALL', 'W9TOWN': '*ALL', 'W9FDTA': '01012020',
    'W9TDTA': '', 'WSHTNM': 'WW414R00', 'WSIQTP': 'SY14AP',
    'WSKYCD': 'T', 'WSWVER': '2'
}

# --- Hampden Name Search Configuration ---
HAMPDEN_NAME_SEARCH_PARAMS = {
    'W9ABR': '*ALL',
    'W9TOWN': '*ALL',
    'W9IXTP': 'A',  # All Parties
    'W9INQ': 'AY',  # All Years
    'AYVAL': ' 1948',
    'CYVAL': '2020',
    'WSHTNM': 'WW401R00',
    'WSIQTP': 'LR01LP', # Result List
    'WSKYCD': 'N',      # Name Search
    'WSWVER': '2'
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
                
                # UPDATED: Extract text from the entire row or the first cell
                row_text = await row.inner_text(timeout=10000)
                
                # Regex to find a unique ID (Book-Page or Instrument Number)
                # Text looks like: "Bk-Pg:24417-378 ... Inst #: 11253 ..."
                bk_pg_match = re.search(r"Bk-Pg:([\d-]+)", row_text)
                inst_match = re.search(r"Inst #: (\d+)", row_text)
                
                if bk_pg_match:
                    doc_id_text = f"Bk-Pg_{bk_pg_match.group(1)}"
                elif inst_match:
                    doc_id_text = f"Inst_{inst_match.group(1)}"
                else:
                    # Fallback unique ID
                    doc_id_text = f"Doc_{page_count}_{i+1}_{int(asyncio.get_event_loop().time())}"
                    
                print(f"    Found document: {doc_id_text}")
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


def generate_hampden_name_url(name: str, first_name: str = ""):
    """Generates a search URL for Hampden County ALIS system by name."""
    # Order and inclusion of empty params seems to matter for ALIS
    base = "https://search.hampdendeeds.com/ALIS/WW400R.HTM"
    params = [
        f"W9SNM={urllib.parse.quote_plus(name.upper())}",
        f"W9GNM={urllib.parse.quote_plus(first_name.upper())}",
        "W9IXTP=A",
        "W9ABR=*ALL",
        "W9TOWN=*ALL",
        "W9INQ=AY",
        "W9FDTA=",
        "W9TDTA=",
        "AYVAL=+1948",
        "CYVAL=2020",
        "WSHTNM=WW401R00",
        "WSIQTP=LR01LP",
        "WSKYCD=N",
        "WSWVER=2",
        "W9INQ="
    ]
    return f"{base}?{'&'.join(params)}#schTerms"


async def scrape_hampshire_by_name(
    context: BrowserContext, 
    last_name: str, 
    first_name: str = "",
    download_dir: Path = None,
    list_only: bool = False
) -> list[dict]:
    """
    Scrapes Hampshire County Registry of Deeds using the Perfect Vision system.
    Uses ASP.NET postback-based search (not URL params).
    
    Args:
        context: Playwright browser context
        last_name: Business name or last name to search (e.g., "EXAMPLE REALTY" or "LASTNAME")
        first_name: First name for individual searches (optional)
        download_dir: Directory to save downloaded documents (None = list only)
        list_only: If True, just return list of documents found, don't download
        
    Returns:
        List of dicts with document info: {book_page, doc_type, street, description, file_date}
    """
    config = COUNTY_CONFIG['hampshire']
    page = await context.new_page()
    results = []
    
    print(f"\n🔍 Hampshire Registry Search: {last_name}" + (f", {first_name}" if first_name else ""))
    await page.goto(config['base_url'], wait_until="domcontentloaded")
    
    # Handle Disclaimer/Accept Page if present
    try:
        accept_btn = page.locator('input[value="I Accept"], input[value="Accept"], a:has-text("Search Records")')
        if await accept_btn.is_visible(timeout=5000):
            print("  ℹ️ Found Disclaimer page, clicking Accept...")
            await accept_btn.click()
            await page.wait_for_load_state("domcontentloaded")
    except:
        pass

    # Wait for search form to be ready
    try:
        await page.wait_for_selector(f"#{config['field_ids']['last_name']}", state="visible", timeout=30000)
    except TimeoutError:
        print("  ❌ Timeout waiting for search form")
        return []
        
    await page.wait_for_timeout(1000)  # Extra stabilization
    
    # Fill and submit search form via JavaScript (more reliable than clicking)
    search_js = f"""
    (() => {{
        const lastNameField = document.getElementById('{config['field_ids']['last_name']}');
        const firstNameField = document.getElementById('{config['field_ids']['first_name']}');
        const searchBtn = document.getElementById('{config['field_ids']['search_btn']}');
        
        if (lastNameField) lastNameField.value = '{last_name.upper()}';
        if (firstNameField) firstNameField.value = '{first_name.upper()}';
        if (searchBtn) searchBtn.click();
        return true;
    }})()
    """
    await page.evaluate(search_js)
    await page.wait_for_timeout(3000)  # Wait for results
    
    # Handle "too many results" popup (over 1000 records) - must click OK to see results
    popup_appeared = False
    try:
        # The MessageBox uses specific ASP.NET control IDs
        ok_btn = page.locator('#MessageBoxCtrl1_buttonmbatCLIENTOK')
        if await ok_btn.is_visible(timeout=2000):
            print("  ℹ️ Search limited to first 1000 records, clicking OK...")
            await ok_btn.click()
            await page.wait_for_timeout(2000)
            popup_appeared = True
    except:
        pass  # No popup is fine
    
    # Site bug workaround: After dismissing 1000+ popup, grid doesn't appear
    # Re-trigger search to force the grid to load
    if popup_appeared:
        print("  🔄 Re-triggering search to load results grid...")
        await page.evaluate(search_js)
        await page.wait_for_timeout(3000)
        # Handle popup again if it reappears
        try:
            ok_btn = page.locator('#MessageBoxCtrl1_buttonmbatCLIENTOK')
            if await ok_btn.is_visible(timeout=2000):
                await ok_btn.click()
                await page.wait_for_timeout(2000)
        except:
            pass
    
    # Extract results from all pages (handle pagination)
    extract_js = """
    (() => {
        const results = [];
        const container = document.getElementById('DocList1_ContentContainer1');
        if (!container) return [];
        
        const rows = container.querySelectorAll('table tr');
        
        # Skip the first row (header)
        for (let i = 1; i < rows.length; i++) {
            const row = rows[i];
            # Check if it's a data row (has a Book link)
            const bookLink = row.querySelector('a[id*="ButtonRow_Book_"]');
            if (bookLink) {
                results.push({
                    book: bookLink.innerText.trim(),
                    page: row.querySelector('a[id*="ButtonRow_Page_"]')?.innerText?.trim() || '',
                    book_page: bookLink.innerText.trim() + '/' + (row.querySelector('a[id*="ButtonRow_Page_"]')?.innerText?.trim() || ''),
                    doc_type: row.querySelector('a[id*="ButtonRow_Type Desc._"]')?.innerText?.trim() || '',
                    file_date: row.querySelector('a[id*="ButtonRow_File Date_"]')?.innerText?.trim() || '',
                    street: row.querySelector('a[id*="ButtonRow_Street"]')?.innerText?.trim() || '',
                    description: row.querySelector('a[id*="ButtonRow_Property Descr_"]')?.innerText?.trim() || '',
                    name: row.querySelector('a[id*="ButtonRow_Name_"]')?.innerText?.trim() || ''
                });
            }
        }
        return results;
    })()
    """
    
    # Pagination loop - extract from all pages
    page_num = 1
    while True:
        extracted = await page.evaluate(extract_js)
        if extracted:
            results.extend(extracted)
            if page_num == 1:
                print(f"  ✅ Page {page_num}: {len(extracted)} records")
            else:
                print(f"  📄 Page {page_num}: {len(extracted)} records (total: {len(results)})")
        else:
            if page_num == 1:
                print("  ⚠️ No records found or could not extract results")
            break
        
        # Check if Next button exists and is clickable
        has_next = await page.evaluate("""
            (() => {
                const nextBtn = document.getElementById('DocList1_LinkButtonNext');
                return nextBtn && nextBtn.offsetParent !== null;  # visible
            })()
        """)
        
        if not has_next:
            break  # No more pages
        
        # Click Next and wait for page update
        await page.evaluate("document.getElementById('DocList1_LinkButtonNext').click();")
        await page.wait_for_timeout(2000)  # Wait for ASP.NET postback
        page_num += 1
        
        # Safety limit to prevent infinite loops
        if page_num > 100:
            print("  ⚠️ Reached page limit (100), stopping pagination")
            break
    
    if results:
        print(f"  📊 Total: {len(results)} records across {page_num} page(s)")
        for doc in results[:10]:  # Show first 10
            print(f"    • {doc.get('book_page', 'N/A'):15} | {doc.get('doc_type', 'N/A'):15} | {doc.get('street', 'N/A')}")
        if len(results) > 10:
            print(f"    ... and {len(results) - 10} more")
    
    # Download documents via bulk basket approach
    if not list_only and download_dir and extracted:
        print(f"\n📥 Bulk downloading {len(extracted)} documents via Basket...")
        download_dir.mkdir(parents=True, exist_ok=True)
        
        try:
            # Step 1: Click "Select All" to select all documents
            await page.evaluate("""
                document.getElementById('DocList1_SelectAll')?.click();
            """)
            await page.wait_for_timeout(1500)
            print("    ✓ Selected all documents")
            
            # Step 2: Click bulk "Add to Basket" button (adds all selected)
            await page.evaluate("""
                document.getElementById('DocList1_ButAddToBasket')?.click();
            """)
            await page.wait_for_timeout(2000)
            
            # Step 3: Select "All Pages" in the criteria dialog and click Next
            await page.evaluate("""
                const selectAll = document.getElementById('OrderCriteriaCtrl1_CriteriaCtrl_RadioButton_SelectAll');
                if (selectAll) selectAll.click();
                setTimeout(() => {
                    const nextBtn = document.getElementById('OrderCriteriaCtrl1_ImageButton_Next');
                    if (nextBtn) nextBtn.click();
                }, 500);
            """)
            await page.wait_for_timeout(3000)
            print("    ✓ Added to basket with 'All Pages'")
            
            # Step 4: Navigate to Basket
            await page.evaluate("""
                document.getElementById('Navigator1_Basket')?.click();
            """)
            await page.wait_for_timeout(2000)
            
            # Step 5: Select all items IN the basket (important!)
            await page.evaluate("""
                document.getElementById('BasketCtrl1_SelectAll')?.click();
            """)
            await page.wait_for_timeout(1000)
            
            # Step 6: Click the PDF download button
            await page.evaluate("""
                document.getElementById('BasketCtrl1_LinkButtonDownloadPDF')?.click();
            """)
            await page.wait_for_timeout(3000)
            print("    ✓ Initiated PDF download from basket")
            
            # Step 7: Handle the Download Wizard popup
            await page.wait_for_timeout(2000)  # Give popup time to open
            wizard_page = None
            for p in context.pages:
                # Check for various wizard URL patterns
                if p != page and ('Wizard' in p.url or 'Download' in p.url):
                    wizard_page = p
                    break
            
            if not wizard_page:
                # Try again after another wait
                await page.wait_for_timeout(2000)
                for p in context.pages:
                    if p != page:
                        wizard_page = p
                        break
            
            if wizard_page:
                await wizard_page.wait_for_load_state("networkidle", timeout=20000)
                print(f"    ✓ Found wizard page: {wizard_page.url[:60]}...")
                
                # Debug: Log ALL clickable/input elements to understand page structure
                all_inputs = await wizard_page.evaluate("""
                    (() => {
                        const results = [];
                        // Get all radio buttons with surrounding context
                        document.querySelectorAll('input[type="radio"]').forEach(r => {
                            // Look at parent and sibling elements for context
                            const parent = r.parentElement;
                            const grandparent = parent?.parentElement;
                            const siblingText = parent?.innerText?.substring(0, 80) || '';
                            const grandparentText = grandparent?.innerText?.substring(0, 100) || '';
                            results.push({
                                id: r.id, 
                                name: r.name,
                                siblingText: siblingText.trim(),
                                grandparentText: grandparentText.replace(/\\n/g, ' ').trim().substring(0, 80),
                                checked: r.checked
                            });
                        });
                        return results;
                    })()
                """)
                print(f"    🔍 All radio inputs with context: {all_inputs}")
                
                # STEP 1: Select the "Non-Subscribers" radio button
                # STEP 1: Select the "Non-Subscribers" radio button
                # We know the ID from logs: PaymentOptions1_RadioButton_CCOption
                await wizard_page.evaluate("""
                    (() => {
                        const r = document.getElementById('PaymentOptions1_RadioButton_CCOption');
                        if (r) {
                            r.click();
                            r.checked = true; // Force it visually/logically just in case
                        }
                    })()
                """)
                print("    ✓ Selected Non-Subscribers option (CCOption)")
                
                # Double check if it took
                is_checked = await wizard_page.evaluate("document.getElementById('PaymentOptions1_RadioButton_CCOption')?.checked")
                if not is_checked:
                      print("    ⚠️ Warning: CCOption not checked after click! Trying again...")
                      await wizard_page.click('#PaymentOptions1_RadioButton_CCOption')
                
                await wizard_page.wait_for_timeout(1500)
                
                # ROBUST WIZARD NAVIGATION: Click Payment Options Next ONCE and wait for Order Report
                print("    👉 Clicking Payment Options 'Next' (once) and waiting...")
                await wizard_page.evaluate("""
                    const btn1 = document.getElementById('PaymentOptions1__NextBtn');
                    const btn2 = document.getElementById('PaymentOptions1_NextBtn');
                    if (btn1) btn1.click();
                    else if (btn2) btn2.click();
                """)
                
                # Wait for the Order Report page to load (can be slow)
                print("    ⏳ Waiting for Order Report page (up to 120s)...")
                try:
                    # Wait for an element identifying the next page, or the download link directly
                    # The Order report page usually has "OrderReport" in IDs
                    await wizard_page.wait_for_selector(
                          '[id*="OrderReport"]', 
                          state="visible", 
                          timeout=120000 
                    )
                    print("    ✓ Reached Order Report step!")
                    reached_order_report = True
                except Exception as e:
                    print(f"    ⚠️ Warning: Timeout waiting for Order Report: {e}")
                    # We might have skipped to download or something else happening?
                    # We'll fall through to the next check.

                # Debug: Log current state after wizard navigation
                step2_elements = await wizard_page.evaluate("""
                    (() => {
                        const elements = [];
                        document.querySelectorAll('input[type="submit"], input[type="button"]').forEach(el => {
                            if (el.offsetParent !== null) {
                                elements.push({id: el.id, text: (el.innerText || el.value || '').substring(0,30)});
                            }
                        });
                        return elements;
                    })()
                """)
                print(f"    🔍 After wizard nav - visible buttons: {step2_elements}")
                
                # Try multiple possible button IDs for the Order Report/next step
                order_clicked = False
                possible_next_buttons = [
                    'OrderReport1__NextBtn',
                    'OrderReport1_NextBtn',
                    'OrderReportCtrl1__NextBtn', 
                    'OrderReportCtrl1_NextBtn',
                ]
                
                for i in range(30):  # Try for 15 seconds
                    for btn_id in possible_next_buttons:
                        clicked = await wizard_page.evaluate(f"""
                            (() => {{
                                const btn = document.getElementById('{btn_id}');
                                if (btn && btn.offsetParent !== null) {{
                                    btn.click();
                                    return '{btn_id}';
                                }}
                                return null;
                            }})()
                        """)
                        if clicked:
                            print(f"    ✓ Clicked {clicked}")
                            order_clicked = True
                            break
                    if order_clicked:
                        break
                    
                    # After 5 seconds, try broader approach - any visible Next/Order button
                    if i >= 10:
                        any_next = await wizard_page.evaluate("""
                            (() => {
                                // 1. Try any button with "Order" in ID
                                const orderBtns = document.querySelectorAll('[id*="OrderReport"][id*="Btn"]');
                                for (const btn of orderBtns) {
                                    if (btn.offsetParent !== null) {
                                        btn.click();
                                        return 'fuzzy-id: ' + btn.id;
                                    }
                                }
                                
                                // 2. Try any input[type=submit] with "Next" value that isn't Payment
                                const allInputs = document.querySelectorAll('input[type="submit"], input[type="button"]');
                                for (const el of allInputs) {
                                    const val = (el.value || el.innerText || '').toLowerCase();
                                    const id = (el.id || '').toLowerCase();
                                    if ((val.includes('next') || val.includes('order')) && !id.includes('payment') && el.offsetParent !== null) {
                                        el.click();
                                        return 'fallback-text: ' + (el.id || el.value);
                                    }
                                }
                                return null;
                            })()
                        """)
                        if any_next:
                            print(f"    ✓ Clicked via fallback: {any_next}")
                            order_clicked = True
                            break
                    
                    await wizard_page.wait_for_timeout(500)
                
                if not order_clicked:
                    # Enhanced debug: what's currently on the page?
                    page_info = await wizard_page.evaluate("""
                        (() => {
                            const buttons = [];
                            document.querySelectorAll('input, button, a.btn').forEach(el => {
                                if (el.offsetParent !== null) buttons.push(el.tagName + '#' + el.id + '("' + (el.value || el.innerText) + '")');
                            });
                            return document.body.innerText.substring(0, 300) + '\\nVISIBLE BUTTONS: ' + buttons.join(', ');
                        })()
                    """)
                    print(f"    ⚠️ Order Report button not found. Page info: {page_info}...")
                    # Debug: Dump HTML to file
                    html_content = await wizard_page.content()
                    with open(f"wizard_debug_{last_name}.html", "w") as f:
                        f.write(html_content)
                    print(f"    📸 Saved HTML dump to wizard_debug_{last_name}.html")
                
                # Wait for download link to appear (document preparation can take time - up to 300 seconds for large batches)
                print("    ⏳ Waiting for download to be prepared...")
                download_url = None
                for attempt in range(300):  # Try for up to 300 seconds
                    await wizard_page.wait_for_timeout(1000)
                    
                    # Progress indicator every 10 seconds
                    if attempt > 0 and attempt % 10 == 0:
                        print(f"    ⏳ Still waiting... ({attempt}s)")
                    
                    download_url = await wizard_page.evaluate("""
                        (() => {
                            // Primary: look for link with text 'here'
                            const allLinks = document.querySelectorAll('a');
                            for (const el of allLinks) {
                                if (el.innerText.trim().toLowerCase() === 'here') {
                                    const href = el.getAttribute('href');
                                    if (href && href.includes('ACSResource')) return href;
                                }
                            }
                            
                            // Secondary: look for DownloadLink element with href
                            const downloadLink = document.getElementById('DownloadLink');
                            if (downloadLink) {
                                const href = downloadLink.getAttribute('href');
                                if (href && href.includes('ACSResource')) return href;
                            }
                            
                            // Fallback: any link with ACSResource
                            const links = document.querySelectorAll('a[href*="ACSResource"]');
                            for (const el of links) {
                                const href = el.getAttribute('href');
                                if (href) return href;
                            }
                            return null;
                        })()
                    """)
                    if download_url:
                        print(f"    ✓ Found download URL after {attempt+1}s")
                        break
                
                if download_url:
                    full_url = download_url if download_url.startswith('http') else f"https://www.masslandrecords.com/Hampshire/D/{download_url}"
                    cookies = {c['name']: c['value'] for c in await context.cookies()}
                    headers = {'User-Agent': await page.evaluate("navigator.userAgent")}
                    
                    # Download the file (could be PDF or ZIP)
                    response = requests.get(full_url, headers=headers, cookies=cookies, timeout=300)
                    if response.status_code == 200:
                        content_type = response.headers.get('Content-Type', '').lower()
                        content_disp = response.headers.get('Content-Disposition', '')
                        
                        # Default to PDF
                        ext = '.pdf'
                        
                        # robust check: magic bytes for ZIP (PK.. matches 50 4b 03 04)
                        if response.content.startswith(b'PK\x03\x04'):
                            ext = '.zip'
                        elif 'zip' in content_type:
                            ext = '.zip'
                        elif 'filename=' in content_disp:
                             # Try to parse filename from header
                             import re
                             fname_match = re.search(r'filename="?([^"]+)"?', content_disp)
                             if fname_match and fname_match.group(1).lower().endswith('.zip'):
                                 ext = '.zip'
                        name_part = last_name.replace(' ', '_').lower()
                        if first_name:
                            name_part += f"_{first_name.replace(' ', '_').lower()}"
                        filename = f"{name_part}_documents{ext}"
                        filepath = download_dir / filename
                        
                        with open(filepath, 'wb') as f:
                            f.write(response.content)
                        print(f"    ✅ Downloaded: {filename} ({len(response.content) / 1024:.1f} KB)")
                    else:
                        print(f"    ❌ Download failed: HTTP {response.status_code}")
                else:
                    # Debug: print what's on the page
                    page_text = await wizard_page.evaluate("document.body.innerText.substring(0, 500)")
                    print(f"    ❌ Could not find download URL (page: {page_text[:100]}...)")
                
                await wizard_page.close()
            else:
                print("    ⚠️ Download wizard didn't open - documents may need manual download")
                
        except Exception as e:
            print(f"    ❌ Error during bulk download: {str(e)[:80]}")
    
    await page.close()
    return results


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
        description="A tool to scrape documents from a deeds registry, combine, and OCR them.\n\n"
                    "Supports two counties:\n"
                    "  - Hampden (ALIS system): URL-based scraping\n"
                    "  - Hampshire (Perfect Vision): Name-based search\n\n"
                    "Examples:\n"
                    "  python deeds_scraper.py --county hampshire --name 'EXAMPLE CORP'\n"
                    "  python deeds_scraper.py 'https://search.hampdendeeds.com/...'\n"
                    "  python deeds_scraper.py -i properties.csv --generate-urls",
        formatter_class=argparse.RawTextHelpFormatter
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('url', nargs='?', default=None, help="A single starting URL to scrape (Hampden only).")
    group.add_argument('-i', '--input-file', help="Path to a CSV file containing a 'URL' column.")
    group.add_argument('--name', help="Search by name (Hampshire). Use with --county hampshire.")
    parser.add_argument('--generate-urls', action='store_true', help="Pre-processing step: Reads a CSV with 'Property Address' and adds search URLs. Must be used with -i.")
    parser.add_argument('--county', choices=['hampden', 'hampshire'], default='hampden',
                        help="Which county registry to use (default: hampden)")
    parser.add_argument('--first-name', default='', help="First name for Hampshire name search (optional)")
    parser.add_argument('--list-only', action='store_true', help="For Hampshire: just list documents, don't download")
    
    args = parser.parse_args()

    # Handle Hampden name-based search (convert to URL and scrape)
    if args.name and args.county == 'hampden':
        print(f"\n🔍 Hampden Registry Search: {args.name}" + (f", {args.first_name}" if args.first_name else ""))
        search_url = generate_hampden_name_url(args.name, args.first_name)
        print(f"Generated URL: {search_url}")
        
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=False, slow_mo=50)
            context = await browser.new_context(
                viewport={'width': 1920, 'height': 1080},
                user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                accept_downloads=True
            )
            
            # Create a specific download directory for this name
            safe_name = re.sub(r'[\\/*?:"<>|]', "_", args.name.replace(' ', '_')).lower()
            if args.first_name:
                safe_name += f"_{args.first_name.lower()}"
            download_dir = OUTPUT_DIR / f"hampden_scrape_{safe_name}"
            download_dir.mkdir(parents=True, exist_ok=True)
            
            await scrape_url(search_url, context, download_dir)
            await browser.close()
        return

    # Handle Hampshire name-based search
    if args.name:
        if args.county != 'hampshire':
            print("Note: --name search is only supported for Hampshire and Hampden. Defaulting to Hampshire if not specified.")
            args.county = 'hampshire'
        
        OUTPUT_DIR.mkdir(exist_ok=True)
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=False, slow_mo=50)
            context = await browser.new_context(
                viewport={'width': 1920, 'height': 1080},
                user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
            )
            
            results = await scrape_hampshire_by_name(
                context=context,
                last_name=args.name,
                first_name=args.first_name,
                download_dir=OUTPUT_DIR if not args.list_only else None,
                list_only=args.list_only
            )
            
            # Save results to CSV - include first name in filename to avoid collisions
            if results:
                name_part = args.name.replace(' ', '_').lower()
                if args.first_name:
                    name_part += f"_{args.first_name.replace(' ', '_').lower()}"
                output_csv = OUTPUT_DIR / f"hampshire_search_{name_part}.csv"
                with open(output_csv, 'w', newline='', encoding='utf-8') as f:
                    writer = csv.DictWriter(f, fieldnames=results[0].keys())
                    writer.writeheader()
                    writer.writerows(results)
                print(f"\n📄 Results saved to: {output_csv}")
            
            await browser.close()
        return

    if args.generate_urls:
        if not args.input_file:
            print("Error: --generate-urls requires the -i <filename.csv> argument.")
            sys.exit(1)
        generate_urls_in_csv(args.input_file)
        return

    items_to_process = []
    if args.input_file:
        try:
            with open(args.input_file, mode='r', newline='', encoding='utf-8') as f:
                reader = csv.DictReader(f)
                for row in reader:
                    if row.get('search_registry_url'):
                        # Prefer 'search_registry_url' if available (generated column)
                        items_to_process.append({'url': row['search_registry_url'], 'address': row.get('Property Address', '')})
                    elif row.get('URL'):
                        # Fallback to 'URL'
                        items_to_process.append({'url': row['URL'], 'address': row.get('Property Address', '')})
        except (FileNotFoundError, KeyError) as e:
            print(f"Error processing CSV file: {e}")
            sys.exit(1)
    elif args.url:
        # Extract name from Hampden URL params for better filename
        parsed = urllib.parse.urlparse(args.url)
        params = urllib.parse.parse_qs(parsed.query)
        surname = params.get('W9SNM', [''])[0].strip()
        firstname = params.get('W9GNM', [''])[0].strip()
        if surname:
            address_label = f"hampden_{surname.lower().replace(' ', '_')}"
            if firstname:
                address_label += f"_{firstname.lower()}"
        else:
            address_label = 'manual_url_scrape'
        items_to_process.append({'url': args.url, 'address': address_label})

    if not items_to_process:
        print("No valid URLs found to process.")
        return

    OUTPUT_DIR.mkdir(exist_ok=True)
    TEMP_DIR.mkdir(exist_ok=True)

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False, slow_mo=50)
        context = await browser.new_context(accept_downloads=True)

        for i, item in enumerate(items_to_process):
            start_url = item['url']
            address_raw = item['address']
            
            # Create a safe filename from address
            if address_raw:
                safe_name = re.sub(r'[\\/*?:"<>| ]', "", address_raw.lower()) # simple sanitization
                safe_name = safe_name.replace("example_term", "example_term") # ensure no weird casing
            else:
                safe_name = f"document_set_{i + 1}"

            run_id = i + 1
            print("=" * 70)
            print(f"🚀 STARTING RUN {run_id}/{len(items_to_process)}: {address_raw}")
            
            # Create a unique temporary subdirectory for this run
            run_temp_dir = TEMP_DIR / f"run_{run_id}"
            run_temp_dir.mkdir(exist_ok=True)
            
            # --- Step 1: Scrape all documents for this URL ---
            await scrape_url(start_url, context, run_temp_dir)

            # --- Step 2: Process the downloaded TIFs ---
            output_pdf_name = OUTPUT_DIR / f"{safe_name}.pdf"
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

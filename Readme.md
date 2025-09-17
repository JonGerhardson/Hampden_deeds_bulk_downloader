Registry of Deeds Document Scraper

This command-line tool automates a complete web scraping and document processing workflow for a county registry of deeds website.
Features

    Multi-Mode Input: Process a single starting URL or a CSV file containing multiple URLs.

    Automatic Pagination: The scraper automatically clicks through all "Next" pages to find and download every document in a search result.

    File Combination: All TIF images downloaded from a single starting URL are combined into one document.

    OCR Integration: The combined document is processed with OCR (Optical Character Recognition) to create a fully searchable PDF.

    Organized Output: The final output includes the searchable PDF and a separate folder containing all the original TIF files for each run.

    URL Generation Utility: Includes a pre-processing mode to enrich a CSV file with property addresses by generating the correct search URLs.

⚠️ Prerequisites: Tesseract OCR

This script depends on the Tesseract OCR engine. You MUST install it on your system for the OCR step to work.

    Windows: Download and run the installer from Tesseract at UB Mannheim. Make sure to check the option to add Tesseract to your system's PATH during installation.

    macOS (using Homebrew):

    brew install tesseract

    Linux (Debian/Ubuntu):

    sudo apt-get install tesseract-ocr

Setup

    Create a Project Folder: Create a new folder for this project and place the deeds_scraper.py, requirements.txt, and your data files inside it.

    Install Python Libraries: Open a terminal or command prompt in your project folder and run:

    pip install -r requirements.txt

    Install Playwright Browsers: This only needs to be done once.

    playwright install

How to Use

The script has three main modes of operation.
Mode 1: Process a Single URL

Provide a single search results URL as a command-line argument.

Usage:

python deeds_scraper.py "https://your_starting_search_url_here.com"

Example:

python deeds_scraper.py "[https://search.hampdendeeds.com/ALIS/WW400R.HTM?W9ABR=TT&W9TOWN=CHIC](https://search.hampdendeeds.com/ALIS/WW400R.HTM?W9ABR=TT&W9TOWN=CHIC)..."

This will create final_output/document_set_1_OCR.pdf and a folder final_output/document_set_1_TIFs/.
Mode 2: Process a CSV File

Provide a CSV file that contains a column named URL. The script will process each URL from that column.

Usage:

python deeds_scraper.py -i <your_file.csv>

Example:

python deeds_scraper.py -i takings_with_urls.csv

This will loop through each URL in the CSV, creating a separate PDF and TIF folder for each one (e.g., document_set_1_OCR.pdf, document_set_2_OCR.pdf, etc.).
Mode 3: Generate URLs in a CSV (Pre-processing)

If you have a CSV with a Property Address column, this mode will generate the search URLs and add them to a new search_registry_url column. This modifies your CSV file in-place.

Usage:

python deeds_scraper.py -i <your_file.csv> --generate-urls

Example:

python deeds_scraper.py -i takings.csv --generate-urls

After running this, your takings.csv will be ready to use in Mode 2.
Output Structure

All final files will be placed in a final_output directory. For each input URL processed, you will get:

    A searchable PDF: document_set_1_OCR.pdf

    A folder with the original images: document_set_1_TIFs/

This script automates the retrieval of property deed documents from the Hampden and Hampshire County (Massachusetts) registries, downloads the images, and merges them into OCR-processed, text-searchable PDF files.
Setup

Prerequisites

    Python 3.8+

    Tesseract OCR (Required for PDF generation)

        macOS: brew install tesseract

        Ubuntu/Debian: sudo apt install tesseract-ocr

        Windows: Install the Tesseract binary and ensure it is in your system PATH.

Installation

    Clone or download this repository.

    Install the required Python packages:
    Bash

pip install playwright ocrmypdf pillow requests

Install the Playwright browser binaries:
Bash

    playwright install chromium

Usage

Run the script from the command line using one of the following modes.

Mode 1: Search by Name (Hampshire or Hampden) Searches the registry for a specific name, downloads all associated documents, and processes them.
Bash

# Hampshire County Search
```python deeds_scraper.py --county hampshire --name "EXAMPLE CORP"```

# Hampden County Search
```python deeds_scraper.py --county hampden --name "DOE" --first-name "JOHN"```

Mode 2: Single URL Scrape (Hampden Only) Scrapes all documents found at a specific Hampden registry search result URL.
Bash

```python deeds_scraper.py "https://search.hampdendeeds.com/ALIS/WW400R.HTM?W9SNM=EXAMPLE..."```

Mode 3: Batch Processing via CSV Reads a CSV file containing property addresses, generates search URLs, and downloads documents for each row.

    Prepare Input: Create a CSV file (e.g., input.csv) with a column named Property Address.

    Generate URLs:
    Bash

python deeds_scraper.py -i input.csv --generate-urls

Process Downloads:
Bash

    ```python deeds_scraper.py -i input.csv```

Arguments

    url: The target URL for scraping (Hampden only).

    --county: Selects the registry system (hampden or hampshire). Default: hampden.

    --name: The last name or business name to search.

    --first-name: (Optional) The first name to refine the search.

    -i, --input-file: Path to the input CSV file.

    --generate-urls: Updates the input CSV with search URLs based on addresses.

    --list-only: Lists found documents without downloading (Hampshire only).

Output Processed files are saved to the final_output directory.

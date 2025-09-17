Unlike practicaly every other county registry of deeds website in Massachusetts, hampdendeeds.com does not allow users to download multiple records at one go, making research involving more than just a few documents tedious and time consuming. Even worse, the files are only provided as TIF images, not pdfs with text. This python script takes eithher a single url of search results, or a csv with urls as an input, and automatically downloads all records linked fromn that page, then OCRs the image files and combines them into a single searchable pdf. 

As a bonus it uses Playwright in non-headless mode, forcing you to go take a break while it runs. 

### Quickstart
```
git clone https://github.com/JonGerhardson/Hampden_deeds_bulk_downloader
```
```
cd Hampden_deeds_bulk_downloader
```

**Install dependencies**




This script depends on the Tesseract OCR engine. You MUST install it on your system for the OCR step to work.

Windows: Download and run the installer from Tesseract. Make sure to check the option to add Tesseract to your system's PATH during installation.

macOS (using Homebrew):

    brew install tesseract
    
 Linux (Debian/Ubuntu):

    sudo apt-get install tesseract-ocr

Install Python Libraries: Open a terminal or command prompt in your project folder and run:

    pip install -r requirements.txt

Install Playwright Browsers: This only needs to be done once.

    playwright install

**Usage (single url)**

Go to Hampdendeeds.com and search their records for whatever you are looking for. To avoid extraneous downloads, tailor your search as narowly as possible. For example, check the box that only returns records since 2020 if you only need recent documents. 

In this directory open a terminal and run 
```
python deeds_scraper.py 'search url here'
```

The script will then open an automated browser window and begin downloading the tif files from each listed search result until there are no more results. After it reaches the end, it will combine the images into a single pdf and run tesseract OCR on the file. 

**It's not working!**
Make sure the url from hampden deeds is in quotation marks. 

MIT licensed. Don't use this for anything shady. Don't blame me if anything breaks. 

# A more in-depth but AI generated readme is below use at own risk for more advanced features 
This command-line tool automates a complete web scraping and document processing workflow for a county registry of deeds website.
Features

    Multi-Mode Input: Process a single starting URL or a CSV file containing multiple URLs.

    Automatic Pagination: The scraper automatically clicks through all "Next" pages to find and download every document in a search result.

    File Combination: All TIF images downloaded from a single starting URL are combined into one document.

    OCR Integration: The combined document is processed with OCR (Optical Character Recognition) to create a fully searchable PDF.

    Organized Output: The final output includes the searchable PDF and a separate folder containing all the original TIF files for each run.

    URL Generation Utility: Includes a pre-processing mode to enrich a CSV file with property addresses by generating the correct search URLs.

How to Use

The script has three main modes of operation.

### Mode 1: Process a Single URL

Provide a single search results URL as a command-line argument.

Usage:

python deeds_scraper.py "https://your_starting_search_url_here.com"

Example:
```
python deeds_scraper.py "url"
```
This will create final_output/document_set_1_OCR.pdf and a folder final_output/document_set_1_TIFs/.

### Mode 2: Process a CSV File


Provide a CSV file that contains a column named URL. The script will process each URL from that column.

Usage:
```
python deeds_scraper.py -i <your_file.csv>
```

This will loop through each URL in the CSV, creating a separate PDF and TIF folder for each one (e.g., document_set_1_OCR.pdf, document_set_2_OCR.pdf, etc.).

### Mode 3: Generate URLs in a CSV (Pre-processing)


If you have a CSV with a Property Address column, this mode will generate the search URLs and add them to a new search_registry_url column. This modifies your CSV file in-place.

Usage:
```
python deeds_scraper.py -i <your_file.csv> --generate-urls
```

After running this, your takings.csv will be ready to use in Mode 2.
Output Structure

All final files will be placed in a final_output directory. For each input URL processed, you will get:

    A searchable PDF: document_set_1_OCR.pdf

    A folder with the original images: document_set_1_TIFs/

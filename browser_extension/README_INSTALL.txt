================================================================================
VEGA SCAM SHIELD & JOB INGESTER — BRAVE / CHROME EXTENSION INSTALLATION GUIDE
================================================================================

This local Manifest V3 extension enables 1-Click capture of job postings directly 
from your active browser tab (LinkedIn, Internshala, Naukri, Workday, Greenhouse, etc.) 
into your local Vega Career Workbench.

It bypasses bot-detection/scraping blocks by extracting the page content locally 
from your open tab and securely transmitting it to Vega on http://127.0.0.1:47632.

--------------------------------------------------------------------------------
INSTALLATION STEPS (Takes 30 seconds):
--------------------------------------------------------------------------------

1. Open Brave Browser (or Chrome / Edge).
2. In the URL address bar, enter:
   brave://extensions   (or chrome://extensions in Chrome)
3. In the top-right corner, turn ON the "Developer mode" toggle.
4. In the top-left corner, click "Load unpacked".
5. In the file picker dialog, navigate to and select this exact folder:
   <your-vega-folder>\browser_extension
6. Click "Select Folder".
7. Pin the "Vega Scam Shield" extension to your browser toolbar (click the puzzle 
   icon in Brave's top bar and click the Pin next to Vega Scam Shield).

--------------------------------------------------------------------------------
HOW TO USE:
--------------------------------------------------------------------------------

1. Ensure Vega is running (Start Vega.bat or python vega_webview.py).
2. Open any job posting in Brave (e.g., LinkedIn Jobs, Internshala, or Naukri).
3. Click the Vega Scam Shield icon in your toolbar.
4. Click "Capture & Check Scam".
5. The extension will run the Scam Shield and skill-matching analysis, display the 
   risk score, and let you jump directly into the Vega Career Workbench to tailor 
   your 1-page ATS resume.

================================================================================

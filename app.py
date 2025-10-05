import os
from typing import Any

from flask import Flask, jsonify, Response, request, render_template_string
import folium
import requests
from dotenv import load_dotenv
import datetime
import json
import time
import math
import rasterio
from rasterio.transform import rowcol
import numpy as np

# Download population data if not exists
TIF_FILE = "gpw_v4_population_count_rev11_2020_30_sec.tif"
GDRIVE_FILE_ID = "1RulG4qIXOryaXR2vKUt0P2DyFhCy07Nk"  # Replace with your actual file ID

if not os.path.exists(TIF_FILE):
    print("Downloading population data from Google Drive...")
    try:
        import gdown  # Import only when needed
        url = f"https://drive.google.com/uc?id={GDRIVE_FILE_ID}"
        gdown.download(url, TIF_FILE, quiet=False)
        print("Download complete!")
    except Exception as e:
        print(f"Error downloading file: {e}")
        print("Continuing without population data...")

# Try to open dataset
try:
    dataset = rasterio.open(TIF_FILE)
    print("Population dataset loaded successfully!")
except Exception as e:
    print(f"WARNING: Could not load population data: {e}")
    dataset = None

load_dotenv()
# ... rest of your code

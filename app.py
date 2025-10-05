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

dataset = rasterio.open("../data/gpw_v4_population_count_rev11_2020_30_sec.tif")

load_dotenv()

app = Flask(__name__)
NASA_API_KEY = os.getenv("NEO_API_KEY")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")


# --- CORS headers for streaming ---
@app.after_request
def after_request(response):
    response.headers.add('Access-Control-Allow-Origin', '*')
    response.headers.add('Access-Control-Allow-Headers', 'Content-Type')
    response.headers.add('Cache-Control', 'no-cache, no-store, must-revalidate')
    return response


def get_population_in_radius(lat, lon, radius_km):
    """
    Query GPW v4 raster to get population count within a circular radius.
    """
    try:
        lat_degrees = radius_km / 111.0
        lon_degrees = radius_km / (111.0 * math.cos(math.radians(lat)))

        min_lon = lon - lon_degrees
        max_lon = lon + lon_degrees
        min_lat = lat - lat_degrees
        max_lat = lat + lat_degrees

        window = rasterio.windows.from_bounds(
            min_lon, min_lat, max_lon, max_lat,
            dataset.transform
        )

        data = dataset.read(1, window=window, masked=True)

        if data.size == 0:
            return 0

        window_transform = dataset.window_transform(window)

        rows, cols = np.meshgrid(
            np.arange(data.shape[0]),
            np.arange(data.shape[1]),
            indexing='ij'
        )

        xs, ys = rasterio.transform.xy(window_transform, rows.flatten(), cols.flatten())
        xs = np.array(xs).reshape(data.shape)
        ys = np.array(ys).reshape(data.shape)

        distances = np.sqrt(
            ((xs - lon) * 111.0 * math.cos(math.radians(lat))) ** 2 +
            ((ys - lat) * 111.0) ** 2
        )

        mask = distances <= radius_km

        if isinstance(data, np.ma.MaskedArray):
            masked_data = np.ma.array(data, mask=~mask | data.mask)
        else:
            masked_data = np.ma.array(data, mask=~mask)

        population = float(np.ma.sum(masked_data))
        return max(0, population)

    except Exception as e:
        print(f"Error reading population data: {e}")
        return 0


def calculate_impact_casualties(lat, lon, diameter_m, mass_kg, velocity_kmh):
    """
    Calculate casualties from meteor impact using GPW v4 population data.
    """
    velocity_m_s = velocity_kmh * 1000 / 3600
    kinetic_energy = 0.5 * mass_kg * velocity_m_s ** 2

    # Crater
    crater_diameter_m = diameter_m * 15
    crater_radius_km = crater_diameter_m / 2000

    # Shockwave
    shockwave_radius_km = math.pow(kinetic_energy, 1 / 3) * 0.05 / 1000

    # Seismic zones
    magnitude = (2 / 3) * math.log10(kinetic_energy / 1000) - 3.2
    strong_shaking_radius_km = math.pow(10, 0.5 * magnitude - 2.0)
    moderate_shaking_radius_km = math.pow(10, 0.5 * magnitude - 1.3)
    light_shaking_radius_km = math.pow(10, 0.5 * magnitude - 0.8)

    # Tsunami
    rho = 1000
    g = 9.81
    k = 0.18
    initial_wave_height = k * math.pow(kinetic_energy / (rho * g), 0.25)
    tsunami_radius_km = 500 * (diameter_m / 1000)

    print(f"Calculating casualties for impact at ({lat}, {lon})")

    # Get cumulative populations
    pop_crater = get_population_in_radius(lat, lon, crater_radius_km)
    pop_shockwave = get_population_in_radius(lat, lon, shockwave_radius_km)
    pop_strong_seismic = get_population_in_radius(lat, lon, strong_shaking_radius_km)
    pop_moderate_seismic = get_population_in_radius(lat, lon, moderate_shaking_radius_km)
    pop_light_seismic = get_population_in_radius(lat, lon, light_shaking_radius_km)

    # Calculate deaths (incremental populations)
    crater_deaths = int(pop_crater)
    shockwave_deaths = int(max(0, (pop_shockwave - pop_strong_seismic) * 0.3))
    strong_seismic_deaths = int(max(0, (pop_strong_seismic - pop_crater) * 0.8))

    total_deaths = crater_deaths + shockwave_deaths + strong_seismic_deaths

    print(f"Total deaths: {total_deaths:,}")

    return {
        "total_deaths": total_deaths,
        "crater_deaths": crater_deaths,
        "shockwave_deaths": shockwave_deaths,
        "strong_seismic_deaths": strong_seismic_deaths,
        "crater_radius_km": round(crater_radius_km, 3),
        "crater_diameter_m": round(crater_diameter_m, 2),
        "shockwave_radius_km": round(shockwave_radius_km, 2),
        "strong_shaking_radius_km": round(strong_shaking_radius_km, 2),
        "moderate_shaking_radius_km": round(moderate_shaking_radius_km, 2),
        "light_shaking_radius_km": round(light_shaking_radius_km, 2),
        "tsunami_wave_height_m": round(initial_wave_height, 2),
        "tsunami_radius_km": round(tsunami_radius_km, 2),
        "impact_energy_joules": kinetic_energy,
        "earthquake_magnitude": round(magnitude, 2),
        "pop_crater": int(pop_crater),
        "pop_shockwave": int(pop_shockwave),
        "pop_strong_seismic": int(pop_strong_seismic),
        "pop_moderate_seismic": int(pop_moderate_seismic),
        "pop_light_seismic": int(pop_light_seismic)
    }


# --- Asteroid generator ---
def generate_asteroids():
    count = 0
    start_time = time.time()
    timeout = 60

    end_date = datetime.date.today() - datetime.timedelta(days=7)
    start_date = datetime.date(2015, 1, 1)
    current_date = end_date

    while current_date >= start_date and count < 20:
        elapsed = time.time() - start_time
        if elapsed > timeout:
            break

        batch_start = max(current_date - datetime.timedelta(days=6), start_date)
        url = f"https://api.nasa.gov/neo/rest/v1/feed?start_date={batch_start}&end_date={current_date}&api_key={NASA_API_KEY}"

        try:
            r = requests.get(url, timeout=10)
            if r.status_code != 200:
                current_date = batch_start - datetime.timedelta(days=1)
                continue

            data = r.json()
            for date_key in data.get("near_earth_objects", {}):
                if count >= 20 or time.time() - start_time > timeout:
                    break

                for ast in data["near_earth_objects"][date_key]:
                    if count >= 20 or time.time() - start_time > timeout:
                        break
                    if ast.get("is_potentially_hazardous_asteroid") and ast.get("close_approach_data"):
                        miss_distance_km = float(ast["close_approach_data"][0]["miss_distance"]["kilometers"])
                        if miss_distance_km < 100_000_000:
                            diameter = ast["estimated_diameter"]["meters"]["estimated_diameter_max"]
                            radius_m = diameter / 2.0
                            volume_m3 = (4 / 3) * math.pi * (radius_m ** 3)
                            assumed_density = 2000.0
                            mass_kg = volume_m3 * assumed_density

                            asteroid_data = {
                                "name": ast["name"],
                                "id": ast["id"],
                                "diameter": diameter,
                                "mass_kg": mass_kg,
                                "assumed_density_kg_m3": assumed_density,
                                "miss_distance_km": miss_distance_km,
                                "date": date_key,
                                "is_hazardous": True,
                                "velocity_kmh": float(
                                    ast["close_approach_data"][0]["relative_velocity"]["kilometers_per_hour"])
                            }
                            count += 1
                            yield asteroid_data

        except Exception as e:
            current_date = batch_start - datetime.timedelta(days=1)
            continue

        current_date = batch_start - datetime.timedelta(days=1)


# --- Landing Page Route ---
@app.route('/')
def landing():
    landing_html = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>The Asteroid Impactor</title>
    <style>
        * {
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }

        body {
            font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
            background: linear-gradient(135deg, #0f1419 0%, #1a1f3a 50%, #0f1419 100%);
            color: white;
            min-height: 100vh;
            overflow-x: hidden;
            position: relative;
        }

        .stars {
            position: fixed;
            top: 0;
            left: 0;
            width: 100%;
            height: 100%;
            pointer-events: none;
            z-index: 0;
        }

        .star {
            position: absolute;
            background: white;
            border-radius: 50%;
            animation: twinkle 3s infinite;
        }

        @keyframes twinkle {
            0%, 100% { opacity: 0.3; }
            50% { opacity: 1; }
        }

        .container {
            max-width: 1400px;
            margin: 0 auto;
            padding: 40px 20px;
            position: relative;
            z-index: 1;
        }

        header {
            text-align: center;
            margin-bottom: 60px;
            animation: fadeInDown 1s ease-out;
        }

        h1 {
            font-size: 4rem;
            background: linear-gradient(135deg, #667eea 0%, #e91e63 50%, #f06292 100%);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
            background-clip: text;
            margin-bottom: 20px;
            font-weight: bold;
        }

        .subtitle {
            font-size: 1.3rem;
            color: #b0b8c9;
            font-weight: 300;
        }

        .stats-grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(300px, 1fr));
            gap: 30px;
            margin-bottom: 60px;
            animation: fadeInUp 1s ease-out 0.3s backwards;
        }

        .stat-card {
            background: rgba(255, 255, 255, 0.05);
            border: 1px solid rgba(255, 255, 255, 0.1);
            border-radius: 15px;
            padding: 30px;
            backdrop-filter: blur(10px);
            transition: all 0.3s ease;
            position: relative;
            overflow: hidden;
        }

        .stat-card::before {
            content: '';
            position: absolute;
            top: 0;
            left: 0;
            right: 0;
            height: 3px;
            background: linear-gradient(90deg, transparent, var(--card-color), transparent);
        }

        .stat-card:hover {
            transform: translateY(-5px);
            box-shadow: 0 10px 30px rgba(0, 0, 0, 0.3);
            border-color: var(--card-color);
        }

        .stat-card.blue { --card-color: #667eea; }
        .stat-card.orange { --card-color: #f59e0b; }
        .stat-card.purple { --card-color: #9b59b6; }

        .stat-icon {
            font-size: 3rem;
            margin-bottom: 15px;
        }

        .stat-value {
            font-size: 3rem;
            font-weight: bold;
            margin-bottom: 10px;
            color: var(--card-color);
        }

        .stat-label {
            font-size: 1.1rem;
            color: #b0b8c9;
            font-weight: 500;
        }

        .section-title {
            font-size: 2.5rem;
            margin-bottom: 30px;
            text-align: center;
            animation: fadeIn 1s ease-out 0.6s backwards;
        }

        .cta-container {
            text-align: center;
            margin: 60px 0;
            animation: fadeIn 1s ease-out 0.9s backwards;
        }

        .cta-button {
            display: inline-flex;
            align-items: center;
            gap: 15px;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: white;
            padding: 20px 50px;
            font-size: 1.3rem;
            border: none;
            border-radius: 50px;
            cursor: pointer;
            transition: all 0.3s ease;
            text-decoration: none;
            font-weight: bold;
            box-shadow: 0 10px 30px rgba(102, 126, 234, 0.4);
        }

        .cta-button:hover {
            transform: scale(1.05);
            box-shadow: 0 15px 40px rgba(102, 126, 234, 0.6);
        }

        .asteroid-table-container {
            background: rgba(255, 255, 255, 0.05);
            border: 1px solid rgba(255, 255, 255, 0.1);
            border-radius: 15px;
            padding: 30px;
            backdrop-filter: blur(10px);
            animation: fadeInUp 1s ease-out 1.2s backwards;
            overflow-x: auto;
        }

        table {
            width: 100%;
            border-collapse: collapse;
            color: white;
        }

        thead {
            background: rgba(102, 126, 234, 0.2);
        }

        th {
            padding: 15px;
            text-align: left;
            font-weight: 600;
            border-bottom: 2px solid #667eea;
        }

        td {
            padding: 15px;
            border-bottom: 1px solid rgba(255, 255, 255, 0.1);
        }

        tbody tr {
            transition: background 0.2s ease;
        }

        tbody tr:hover {
            background: rgba(255, 255, 255, 0.05);
        }

        .hazard-badge {
            display: inline-block;
            background: #e74c3c;
            color: white;
            padding: 5px 12px;
            border-radius: 20px;
            font-size: 0.85rem;
            font-weight: bold;
        }

        .loading {
            text-align: center;
            padding: 40px;
            font-size: 1.2rem;
            color: #b0b8c9;
        }

        .spinner {
            border: 4px solid rgba(255, 255, 255, 0.1);
            border-top: 4px solid #667eea;
            border-radius: 50%;
            width: 50px;
            height: 50px;
            animation: spin 1s linear infinite;
            margin: 0 auto 20px;
        }

        @keyframes spin {
            0% { transform: rotate(0deg); }
            100% { transform: rotate(360deg); }
        }

        @keyframes fadeInDown {
            from {
                opacity: 0;
                transform: translateY(-30px);
            }
            to {
                opacity: 1;
                transform: translateY(0);
            }
        }

        @keyframes fadeInUp {
            from {
                opacity: 0;
                transform: translateY(30px);
            }
            to {
                opacity: 1;
                transform: translateY(0);
            }
        }

        @keyframes fadeIn {
            from { opacity: 0; }
            to { opacity: 1; }
        }

        @media (max-width: 768px) {
            h1 {
                font-size: 2.5rem;
            }

            .subtitle {
                font-size: 1rem;
            }

            .stats-grid {
                grid-template-columns: 1fr;
            }
        }
    </style>
</head>
<body>
    <div class="stars" id="stars"></div>

    <div class="container">
        <header>
            <h1>The Asteroid Impactor</h1>
            <p class="subtitle">Real-time Near-Earth Object Tracking powered by NASA</p>
        </header>

        <div class="stats-grid">
            <div class="stat-card blue">
                <div class="stat-icon">🚀</div>
                <div class="stat-value" id="total-neos">0</div>
                <div class="stat-label">Total NEOs Today</div>
            </div>
            <div class="stat-card orange">
                <div class="stat-icon">⚠️</div>
                <div class="stat-value" id="hazardous-neos">0</div>
                <div class="stat-label">Potentially Hazardous</div>
            </div>
            <div class="stat-card purple">
                <div class="stat-icon">🌐</div>
                <div class="stat-value">24/7</div>
                <div class="stat-label">Live Monitoring</div>
            </div>
        </div>

        <div class="cta-container">
            <a href="/map" class="cta-button">
                <span>🌍</span>
                <span>Launch Interactive Impact Simulator</span>
            </a>
        </div>

        <h2 class="section-title">Today's Near-Earth Asteroids</h2>

        <div class="asteroid-table-container">
            <div id="loading" class="loading">
                <div class="spinner"></div>
                <p>Loading asteroid data from NASA...</p>
            </div>
            <table id="asteroid-table" style="display: none;">
                <thead>
                    <tr>
                        <th>Name</th>
                        <th>Diameter (m)</th>
                        <th>Mass (MT)</th>
                        <th>Velocity (km/h)</th>
                        <th>Miss Distance</th>
                        <th>Date</th>
                        <th>Status</th>
                    </tr>
                </thead>
                <tbody id="asteroid-tbody">
                </tbody>
            </table>
        </div>
    </div>

    <script>
        function createStars() {
            const starsContainer = document.getElementById('stars');
            const numStars = 100;

            for (let i = 0; i < numStars; i++) {
                const star = document.createElement('div');
                star.className = 'star';
                star.style.left = Math.random() * 100 + '%';
                star.style.top = Math.random() * 100 + '%';
                star.style.width = Math.random() * 3 + 'px';
                star.style.height = star.style.width;
                star.style.animationDelay = Math.random() * 3 + 's';
                starsContainer.appendChild(star);
            }
        }

        createStars();

        function formatMassMT(mass_kg) {
            if (mass_kg === undefined || mass_kg === null) return 'N/A';
            return (Number(mass_kg) / 1e9).toLocaleString(undefined, {maximumFractionDigits: 2}) + ' MT';
        }

        async function fetchNASAAsteroids() {
            const NASA_API_KEY = '{{ nasa_api_key }}';
            const today = new Date();
            const endDate = new Date(today);
            endDate.setDate(today.getDate() + 7);

            const startDateStr = today.toISOString().split('T')[0];
            const endDateStr = endDate.toISOString().split('T')[0];

            const url = `https://api.nasa.gov/neo/rest/v1/feed?start_date=${startDateStr}&end_date=${endDateStr}&api_key=${NASA_API_KEY}`;

            try {
                const response = await fetch(url);
                const data = await response.json();

                let asteroidCount = 0;
                let hazardousCount = 0;
                const tbody = document.getElementById('asteroid-tbody');

                for (const dateKey in data.near_earth_objects) {
                    const asteroids = data.near_earth_objects[dateKey];

                    asteroids.forEach(ast => {
                        asteroidCount++;

                        if (ast.is_potentially_hazardous_asteroid) {
                            hazardousCount++;
                        }

                        const diameter = ast.estimated_diameter.meters.estimated_diameter_max;
                        const radius_m = diameter / 2.0;
                        const volume_m3 = (4 / 3) * Math.PI * Math.pow(radius_m, 3);
                        const assumed_density = 2000.0;
                        const mass_kg = volume_m3 * assumed_density;

                        const closeApproach = ast.close_approach_data[0];
                        const velocity = parseFloat(closeApproach.relative_velocity.kilometers_per_hour);
                        const missDistance = parseFloat(closeApproach.miss_distance.kilometers);

                        const row = tbody.insertRow();
                        row.innerHTML = `
                            <td><strong>${ast.name}</strong></td>
                            <td>${diameter.toFixed(2)}</td>
                            <td>${formatMassMT(mass_kg)}</td>
                            <td>${velocity.toLocaleString(undefined, {maximumFractionDigits: 0})}</td>
                            <td>${(missDistance / 1000).toFixed(0)}k km</td>
                            <td>${dateKey}</td>
                            <td>${ast.is_potentially_hazardous_asteroid ? '<span class="hazard-badge">HAZARDOUS</span>' : 'Safe'}</td>
                        `;
                    });
                }

                document.getElementById('total-neos').textContent = asteroidCount;
                document.getElementById('hazardous-neos').textContent = hazardousCount;

                document.getElementById('loading').style.display = 'none';
                document.getElementById('asteroid-table').style.display = 'table';

            } catch (error) {
                console.error('Error fetching NASA data:', error);
                document.getElementById('loading').innerHTML = '<p style="color: #e74c3c;">Error loading asteroid data from NASA: ' + error.message + '</p>';
            }
        }

        fetchNASAAsteroids();
    </script>
</body>
</html>
    """
    return render_template_string(landing_html, nasa_api_key=NASA_API_KEY)


# --- Map route (interactive impact simulator) ---
@app.route('/map')
def map_view():
    m = folium.Map(location=[20, 0], zoom_start=2)

    custom_html = """
    <style>
    .sidebar {position: fixed; left: 0; top: 0; width: 350px; height: 100%; background-color: #2c3e50; color: white; padding: 20px; padding-bottom: 300px; overflow-y: auto; z-index:1000;}
    .asteroid-item {background-color:#34495e; margin:10px 0; padding:15px; border-radius:5px; cursor:pointer; transition:0.3s;}
    .asteroid-item:hover {background-color:#e74c3c; transform: translateX(5px);}
    .asteroid-item.selected {background-color:#e74c3c; border:2px solid white;}
    .hazard-badge {display:inline-block;background-color:#e74c3c;color:white;padding:2px 6px;border-radius:3px;font-size:10px;margin-top:5px;}
    .impact-button {width:100%; padding:15px; background-color:#27ae60; color:white; border:none; border-radius:5px; font-weight:bold; cursor:pointer; margin-top:20px;}
    .impact-button:disabled {background-color:#95a5a6; cursor:not-allowed;}
    .mitigation-button {width:100%; padding:15px; background-color:#9b59b6; color:white; border:none; border-radius:5px; font-weight:bold; cursor:pointer; margin-top:10px;}
    .mitigation-button:disabled {background-color:#95a5a6; cursor:not-allowed;}
    .death-toll {background-color:#c0392b; padding:15px; border-radius:5px; margin-top:15px;}
    .death-toll h3 {margin:0 0 10px 0; font-size:16px;}
    .death-stat {font-size:11px; margin:5px 0; padding:5px; background-color:#922b21; border-radius:3px;}
    .impact-zone {background-color:#34495e; margin:20px 0; padding:20px; border-radius:8px; border-left:4px solid #e74c3c; cursor:pointer; transition:0.3s;}
    .impact-zone:hover {background-color:#415b76; transform: translateX(3px);}
    .impact-zone.active {background-color:#e74c3c; border-left-color:#fff;}
    .impact-zone h3 {margin-top:0; font-size:18px; color:#ecf0f1;}
    .impact-zone p {margin:8px 0; font-size:14px; line-height:1.6;}
    .back-button {width:100%; padding:12px; background-color:#7f8c8d; color:white; border:none; border-radius:5px; font-weight:bold; cursor:pointer; margin-bottom:20px;}
    .back-button:hover {background-color:#95a5a6;}
    .mitigation-content {background-color:#34495e; padding:15px; border-radius:5px; margin-top:15px; max-height:400px; overflow-y:auto; white-space:pre-wrap; line-height:1.6;}
    .loading-spinner {border: 4px solid #f3f3f3; border-top: 4px solid #9b59b6; border-radius: 50%; width: 30px; height: 30px; animation: spin 1s linear infinite; margin: 20px auto;}
    @keyframes spin { 0% { transform: rotate(0deg); } 100% { transform: rotate(360deg); } }
    #map {margin-left:350px;}
    </style>

    <div class="sidebar" id="sidebar-content">
        <h2>Hazardous Meteor Impacts</h2>
        <div class="status-text" id="status-text">Searching for asteroids...</div>
        <div id="asteroid-list"></div>
        <button class="impact-button" id="impact-btn" disabled>SIMULATE IMPACT</button>
        <div class="info-text">1. Select asteroid<br>2. Click map to place target<br>3. SIMULATE IMPACT</div>
    </div>

    <script>
    var selectedAsteroid=null, waypointMarker=null, waypointLocation=null, map=null, allAsteroids=[];
    var impactLayers = {};
    var currentActiveZone = null;
    var currentImpactData = null;

    function formatMassMT(mass_kg) {
        if (mass_kg === undefined || mass_kg === null) return 'N/A';
        return (Number(mass_kg) / 1e9).toLocaleString(undefined, {maximumFractionDigits: 2}) + ' MT';
    }

    function formatEnergyTNT(energy_joules) {
        if (energy_joules === undefined || energy_joules === null) return 'N/A';
        var kilotons = energy_joules / 4.184e12;
        if (kilotons < 1000) {
            return kilotons.toLocaleString(undefined, {maximumFractionDigits: 2}) + ' Kilotons of TNT';
        } else {
            var megatons = kilotons / 1000;
            return megatons.toLocaleString(undefined, {maximumFractionDigits: 2}) + ' Megatons of TNT';
        }
    }

    function addAsteroid(ast){
        allAsteroids.push(ast);
        var massText = formatMassMT(ast.mass_kg);
        var velocityText = (ast.velocity_kmh !== undefined && ast.velocity_kmh !== null)
            ? Number(ast.velocity_kmh).toLocaleString(undefined, {maximumFractionDigits: 0}) + ' km/h'
            : 'N/A';

        var html = '<div class="asteroid-item" data-name="'+ast.name+'" data-diameter="'+ast.diameter+'" data-mass="'+ast.mass_kg+'" data-velocity="'+ast.velocity_kmh+'">'+
               '<div><strong>'+ast.name+'</strong></div>'+
               '<div>Diameter: '+ast.diameter.toFixed(2)+' m</div>'+
               '<div>Mass: '+massText+'</div>'+
               '<div>Velocity: '+velocityText+'</div>'+
               '<div>Miss Dist: '+(ast.miss_distance_km/1000).toFixed(0)+'k km</div>'+
               '<div>Date: '+ast.date+'</div>'+
               '<span class="hazard-badge">HAZARDOUS</span></div>';

        document.getElementById('asteroid-list').innerHTML += html;
        document.getElementById('status-text').innerHTML = 'Found '+allAsteroids.length+' asteroid(s)... searching...';
    }

    function updateImpactButton(){document.getElementById('impact-btn').disabled=!(selectedAsteroid && waypointLocation);}

    function getPointOnCircle(center, radius, angle) {
        var lat = center.lat + (radius / 111320) * Math.cos(angle * Math.PI / 180);
        var lng = center.lng + (radius / (111320 * Math.cos(center.lat * Math.PI / 180))) * Math.sin(angle * Math.PI / 180);
        return {lat: lat, lng: lng};
    }

    function reattachAsteroidListEvents() {
        document.getElementById('asteroid-list').addEventListener('click', function(e){
            var item = e.target.closest('.asteroid-item'); 
            if(!item) return;
            document.querySelectorAll('.asteroid-item').forEach(i=>i.classList.remove('selected'));
            item.classList.add('selected');
            var name=item.getAttribute('data-name');
            var diameter=parseFloat(item.getAttribute('data-diameter'));
            var mass=parseFloat(item.getAttribute('data-mass'));
            var velocity=parseFloat(item.getAttribute('data-velocity'));
            selectedAsteroid={name:name, diameter:diameter, mass_kg:mass, velocity_kmh:velocity};
            updateImpactButton();
        });

        document.getElementById('impact-btn').addEventListener('click', function() {
            if (!selectedAsteroid || !waypointLocation) return;

            fetch('/calculate_casualties', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({
                    lat: waypointLocation.lat,
                    lon: waypointLocation.lng,
                    diameter: selectedAsteroid.diameter,
                    mass_kg: selectedAsteroid.mass_kg,
                    velocity_kmh: selectedAsteroid.velocity_kmh
                })
            })
            .then(r => r.json())
            .then(casualtyData => {
                impactLayers = {};

                var crater = L.circle(waypointLocation, {
                    radius: casualtyData.crater_diameter_m / 2,
                    color: 'black',
                    fillColor: '#000000',
                    fillOpacity: 1,
                    weight: 3,
                    interactive: false
                }).addTo(map);

                var craterLabelPos = getPointOnCircle(waypointLocation, casualtyData.crater_diameter_m / 2, 45);
                var craterLabel = L.marker([craterLabelPos.lat, craterLabelPos.lng], {
                    icon: L.divIcon({
                        className: 'impact-label',
                        html: '<div style="background:rgba(0,0,0,0.8);color:white;padding:5px;border-radius:3px;font-size:11px;font-weight:bold;white-space:nowrap;">Crater: ' + (casualtyData.crater_diameter_m).toFixed(0) + ' m</div>',
                        iconSize: [100, 20]
                    }),
                    interactive: false
                }).addTo(map);

                impactLayers.crater = {layer: crater, label: craterLabel};

                var shockwave = L.circle(waypointLocation, {
                    radius: casualtyData.shockwave_radius_km * 1000,
                    color: '#f1c40f',
                    fillColor: '#f1c40f',
                    fillOpacity: 0.2,
                    weight: 2,
                    interactive: false
                }).addTo(map);

                var shockwaveLabelPos = getPointOnCircle(waypointLocation, casualtyData.shockwave_radius_km * 1000, 90);
                var shockwaveLabel = L.marker([shockwaveLabelPos.lat, shockwaveLabelPos.lng], {
                    icon: L.divIcon({
                        className: 'impact-label',
                        html: '<div style="background:rgba(241,196,15,0.9);color:black;padding:5px;border-radius:3px;font-size:11px;font-weight:bold;white-space:nowrap;">Shockwave: ' + casualtyData.shockwave_radius_km.toFixed(1) + ' km</div>',
                        iconSize: [120, 20]
                    }),
                    interactive: false
                }).addTo(map);

                impactLayers.shockwave = {layer: shockwave, label: shockwaveLabel};

                var lightSeismic = L.circle(waypointLocation, {
                    radius: casualtyData.light_shaking_radius_km * 1000,
                    color: '#e67e22',
                    fillColor: '#e67e22',
                    fillOpacity: 0.12,
                    weight: 1,
                    dashArray: '5, 5',
                    interactive: false
                }).addTo(map);

                var lightSeismicLabelPos = getPointOnCircle(waypointLocation, casualtyData.light_shaking_radius_km * 1000, 180);
                var lightSeismicLabel = L.marker([lightSeismicLabelPos.lat, lightSeismicLabelPos.lng], {
                    icon: L.divIcon({
                        className: 'impact-label',
                        html: '<div style="background:rgba(230,126,34,0.9);color:white;padding:5px;border-radius:3px;font-size:11px;font-weight:bold;white-space:nowrap;">Light Seismic: ' + casualtyData.light_shaking_radius_km.toFixed(1) + ' km</div>',
                        iconSize: [140, 20]
                    }),
                    interactive: false
                }).addTo(map);

                impactLayers.lightSeismic = {layer: lightSeismic, label: lightSeismicLabel};

                var moderateSeismic = L.circle(waypointLocation, {
                    radius: casualtyData.moderate_shaking_radius_km * 1000,
                    color: '#d35400',
                    fillColor: '#d35400',
                    fillOpacity: 0.18,
                    weight: 2,
                    interactive: false
                }).addTo(map);

                var moderateSeismicLabelPos = getPointOnCircle(waypointLocation, casualtyData.moderate_shaking_radius_km * 1000, 225);
                var moderateSeismicLabel = L.marker([moderateSeismicLabelPos.lat, moderateSeismicLabelPos.lng], {
                    icon: L.divIcon({
                        className: 'impact-label',
                        html: '<div style="background:rgba(211,84,0,0.9);color:white;padding:5px;border-radius:3px;font-size:11px;font-weight:bold;white-space:nowrap;">Moderate Seismic: ' + casualtyData.moderate_shaking_radius_km.toFixed(1) + ' km</div>',
                        iconSize: [160, 20]
                    }),
                    interactive: false
                }).addTo(map);

                impactLayers.moderateSeismic = {layer: moderateSeismic, label: moderateSeismicLabel};

                var strongSeismic = L.circle(waypointLocation, {
                    radius: casualtyData.strong_shaking_radius_km * 1000,
                    color: '#c0392b',
                    fillColor: '#c0392b',
                    fillOpacity: 0.25,
                    weight: 2,
                    interactive: false
                }).addTo(map);

                var strongSeismicLabelPos = getPointOnCircle(waypointLocation, casualtyData.strong_shaking_radius_km * 1000, 270);
                var strongSeismicLabel = L.marker([strongSeismicLabelPos.lat, strongSeismicLabelPos.lng], {
                    icon: L.divIcon({
                        className: 'impact-label',
                        html: '<div style="background:rgba(192,57,43,0.9);color:white;padding:5px;border-radius:3px;font-size:11px;font-weight:bold;white-space:nowrap;">Strong Seismic: ' + casualtyData.strong_shaking_radius_km.toFixed(1) + ' km</div>',
                        iconSize: [150, 20]
                    }),
                    interactive: false
                }).addTo(map);

                impactLayers.strongSeismic = {layer: strongSeismic, label: strongSeismicLabel};

                var velocity_m_s = selectedAsteroid.velocity_kmh * 1000 / 3600;
                var kineticEnergy = 0.5 * selectedAsteroid.mass_kg * velocity_m_s * velocity_m_s;
                var RHO_AIR = 1.225;
                var C_SOUND = 343.0;
                var W_tnt_kg = kineticEnergy / 4.184e6;
                var distance_ref_m = 1000;
                var Z = distance_ref_m / Math.pow(W_tnt_kg, 1/3);
                if (Z <= 0) Z = 0.1;
                var delta_p = 1e5 * (1 / Math.pow(Z, 1.8));
                var wind_speed_ms = delta_p / (RHO_AIR * C_SOUND);
                var target_wind_kmh = 60;
                var target_wind_ms = target_wind_kmh / 3.6;
                var Z_target = Math.pow(wind_speed_ms / target_wind_ms, 1 / 1.8) * Z;
                var R_target = Z_target * Math.pow(W_tnt_kg, 1/3);

                var windZone = L.circle(waypointLocation, {
                    radius: R_target,
                    color: '#5dade2',
                    fillColor: '#5dade2',
                    fillOpacity: 0.15,
                    weight: 2,
                    interactive: false
                }).addTo(map);

                var windLabelPos = getPointOnCircle(waypointLocation, R_target, 135);
                var windLabel = L.marker([windLabelPos.lat, windLabelPos.lng], {
                    icon: L.divIcon({
                        className: 'impact-label',
                        html: '<div style="background:rgba(93,173,226,0.9);color:white;padding:5px;border-radius:3px;font-size:11px;font-weight:bold;white-space:nowrap;">Wind Zone: ' + (R_target/1000).toFixed(1) + ' km</div>',
                        iconSize: [120, 20]
                    }),
                    interactive: false
                }).addTo(map);

                impactLayers.wind = {layer: windZone, label: windLabel};

                function is_water(lat, lng) {
                    while (lng > 180) lng -= 360;
                    while (lng < -180) lng += 360;
                    if (lat > 70 || lat < -60) return true;
                    var landMasses = [
                        {latMin: 15, latMax: 72, lngMin: -170, lngMax: -52},
                        {latMin: -56, latMax: 13, lngMin: -82, lngMax: -34},
                        {latMin: 36, latMax: 71, lngMin: -10, lngMax: 40},
                        {latMin: -35, latMax: 37, lngMin: -18, lngMax: 52},
                        {latMin: 0, latMax: 55, lngMin: 60, lngMax: 150},
                        {latMin: -44, latMax: -10, lngMin: 113, lngMax: 154},
                        {latMin: 60, latMax: 83, lngMin: -73, lngMax: -12}
                    ];
                    for (var i = 0; i < landMasses.length; i++) {
                        var land = landMasses[i];
                        if (lat >= land.latMin && lat <= land.latMax && 
                            lng >= land.lngMin && lng <= land.lngMax) {
                            return false;
                        }
                    }
                    return true;
                }

                var tsunamiData = null;
                if (is_water(waypointLocation.lat, waypointLocation.lng)) {
                    var tsunamiCircle = L.circle(waypointLocation, {
                        radius: casualtyData.tsunami_radius_km * 1000,
                        color: '#3498db',
                        fillColor: '#3498db',
                        fillOpacity: 0.15,
                        weight: 2,
                        dashArray: '10, 10',
                        interactive: false
                    }).addTo(map);

                    var tsunamiLabelPos = getPointOnCircle(waypointLocation, casualtyData.tsunami_radius_km * 1000, 315);
                    var tsunamiLabel = L.marker([tsunamiLabelPos.lat, tsunamiLabelPos.lng], {
                        icon: L.divIcon({
                            className: 'impact-label',
                            html: '<div style="background:rgba(52,152,219,0.9);color:white;padding:5px;border-radius:3px;font-size:11px;font-weight:bold;white-space:nowrap;">Tsunami: ' + casualtyData.tsunami_radius_km.toFixed(1) + ' km</div>',
                            iconSize: [120, 20]
                        }),
                        interactive: false
                    }).addTo(map);

                    impactLayers.tsunami = {layer: tsunamiCircle, label: tsunamiLabel};
                    tsunamiData = {
                        waveHeight: casualtyData.tsunami_wave_height_m,
                        radius: casualtyData.tsunami_radius_km,
                        isWater: true
                    };
                }

                if (waypointMarker) {
                    map.removeLayer(waypointMarker);
                    waypointMarker = null;
                }

                currentImpactData = {
                    asteroid: selectedAsteroid,
                    location: waypointLocation,
                    casualtyData: casualtyData,
                    windRadius: R_target / 1000,
                    windSpeed: target_wind_kmh,
                    tsunami: tsunamiData
                };

                showImpactResults(currentImpactData);
            })
            .catch(err => {
                alert('Error calculating casualties: ' + err);
            });
        });
    }

    setTimeout(function(){
        for(var key in window){if(window[key] instanceof L.Map){map=window[key];
            map.on('click', function(e){
                if(waypointMarker){map.removeLayer(waypointMarker);}
                waypointLocation=e.latlng;
                waypointMarker=L.marker(e.latlng).addTo(map);
                waypointMarker.bindPopup('Impact Target<br>Lat:'+e.latlng.lat.toFixed(4)+'<br>Lng:'+e.latlng.lng.toFixed(4)).openPopup();
                updateImpactButton();
            }); break;}
        }
    }, 1000);

    reattachAsteroidListEvents();

    fetch('/stream_asteroids').then(r=>{
        const reader=r.body.getReader(); const decoder=new TextDecoder(); let buffer='';
        function processText(result){
            if(result.done){document.getElementById('status-text').innerHTML='Stream complete. Found '+allAsteroids.length+' hazardous asteroids.'; return;}
            buffer+=decoder.decode(result.value,{stream:true});
            const lines=buffer.split('\\n'); buffer=lines.pop();
            lines.forEach(line=>{
                if(line.startsWith('data: ')){try{const data=JSON.parse(line.substring(6));
                    if(data.asteroid){addAsteroid(data.asteroid);}
                    else if(data.status){document.getElementById('status-text').innerHTML=data.status;}
                }catch(e){console.error(e);}}});
            return reader.read().then(processText);
        }
        reader.read().then(processText);
    });

    function showImpactResults(impactData) {
        var asteroid = impactData.asteroid;
        var location = impactData.location;
        var data = impactData.casualtyData;

        var sidebar = document.getElementById('sidebar-content');
        var html = '<button class="back-button" onclick="resetToAsteroidList()">← Back to Asteroid List</button>';
        html += '<h2>Impact Analysis</h2>';
        html += '<div style="background-color:#34495e; padding:15px; border-radius:5px; margin-bottom:20px;">';
        html += '<strong>' + asteroid.name + '</strong><br>';
        html += 'Diameter: ' + asteroid.diameter.toFixed(2) + ' m<br>';
        html += 'Mass: ' + formatMassMT(asteroid.mass_kg) + '<br>';
        html += 'Velocity: ' + asteroid.velocity_kmh.toLocaleString(undefined, {maximumFractionDigits: 0}) + ' km/h<br>';
        html += 'Impact Energy: ' + formatEnergyTNT(data.impact_energy_joules);
        html += '</div>';

        html += '<div class="death-toll">';
        html += '<h3>💀 CASUALTY ESTIMATE</h3>';
        html += '<div class="death-stat"><strong>TOTAL DEATHS: ' + data.total_deaths.toLocaleString() + '</strong></div>';
        html += '<div class="death-stat">☠️ Crater: ' + data.crater_deaths.toLocaleString() + ' (100% fatality)</div>';
        html += '<div class="death-stat">💨 Shockwave: ' + data.shockwave_deaths.toLocaleString() + ' (30% fatality)</div>';
        html += '<div class="death-stat">🔴 Strong Seismic: ' + data.strong_seismic_deaths.toLocaleString() + ' (80% fatality)</div>';
        html += '<div class="death-stat">🟠 Moderate Seismic: Injuries only</div>';
        html += '<div class="death-stat">🟡 Light Seismic: Minor damage</div>';
        html += '<div class="death-stat">🌪️ Wind Zone: ' + impactData.windRadius.toFixed(2) + ' km (≥60 km/h)</div>';
        if (impactData.tsunami && impactData.tsunami.isWater) {
            html += '<div class="death-stat">🌊 Tsunami: ' + impactData.tsunami.waveHeight.toFixed(2) + ' m wave, ' + impactData.tsunami.radius.toFixed(2) + ' km radius</div>';
        }
        html += '<div class="death-stat" style="margin-top:8px;">⚡ Energy: ' + data.impact_energy_joules.toExponential(2) + ' J</div>';
        html += '<div class="death-stat">🌍 Earthquake: M' + data.earthquake_magnitude + '</div>';
        html += '</div>';

        html += '<button class="mitigation-button" id="mitigation-btn" onclick="getMitigationTactics()">GET MITIGATION TACTICS</button>';
        html += '<div id="mitigation-content"></div>';

        html += '<div class="impact-zone" data-zone="crater">';
        html += '<h3>🎯 IMPACT CRATER</h3>';
        html += '<p><strong>Crater Diameter:</strong> ' + data.crater_diameter_m.toFixed(2) + ' m</p>';
        html += '<p><strong>Deaths:</strong> ' + data.crater_deaths.toLocaleString() + '</p>';
        html += '<p><strong>Population in zone:</strong> ' + data.pop_crater.toLocaleString() + '</p>';
        html += '<p>Complete vaporization at ground zero.</p>';
        html += '</div>';

        html += '<div class="impact-zone" data-zone="shockwave">';
        html += '<h3>💥 SHOCKWAVE ZONE</h3>';
        html += '<p><strong>Radius:</strong> ' + data.shockwave_radius_km.toFixed(2) + ' km</p>';
        html += '<p><strong>Deaths:</strong> ' + data.shockwave_deaths.toLocaleString() + ' (30% fatality)</p>';
        html += '<p><strong>Population in zone:</strong> ' + (data.pop_shockwave - data.pop_strong_seismic).toLocaleString() + '</p>';
        html += '<p>Extreme destruction and widespread fires. All structures obliterated by supersonic blast wave.</p>';
        html += '</div>';

        html += '<div class="impact-zone" data-zone="wind">';
        html += '<h3>🌪️ WIND ZONE (≥60 km/h)</h3>';
        html += '<p><strong>Radius:</strong> ' + impactData.windRadius.toFixed(2) + ' km</p>';
        html += '<p><strong>Wind Speed:</strong> ≥ ' + impactData.windSpeed + ' km/h</p>';
        html += '<p>Outer boundary of destructive winds. Trees uprooted, windows shattered, light structures damaged.</p>';
        html += '</div>';

        html += '<div class="impact-zone" data-zone="strongSeismic">';
        html += '<h3>🔴 STRONG SEISMIC ACTIVITY</h3>';
        html += '<p><strong>Magnitude:</strong> M' + data.earthquake_magnitude.toFixed(2) + '</p>';
        html += '<p><strong>Radius:</strong> ' + data.strong_shaking_radius_km.toFixed(2) + ' km</p>';
        html += '<p><strong>Deaths:</strong> ' + data.strong_seismic_deaths.toLocaleString() + ' (80% fatality)</p>';
        html += '<p><strong>Population in zone:</strong> ' + (data.pop_strong_seismic - data.pop_crater).toLocaleString() + '</p>';
        html += '<p><strong>Intensity:</strong> MMI VII+</p>';
        html += '<p>Significant structural damage. Buildings collapse, ground cracks form, infrastructure fails.</p>';
        html += '</div>';

        html += '<div class="impact-zone" data-zone="moderateSeismic">';
        html += '<h3>🟠 MODERATE SEISMIC ACTIVITY</h3>';
        html += '<p><strong>Magnitude:</strong> M' + data.earthquake_magnitude.toFixed(2) + '</p>';
        html += '<p><strong>Radius:</strong> ' + data.moderate_shaking_radius_km.toFixed(2) + ' km</p>';
        html += '<p><strong>Population in zone:</strong> ' + (data.pop_moderate_seismic - data.pop_strong_seismic).toLocaleString() + '</p>';
        html += '<p><strong>Intensity:</strong> MMI V-VI</p>';
        html += '<p>Felt by everyone. Furniture shifts, weak structures damaged, chimneys collapse.</p>';
        html += '</div>';

        html += '<div class="impact-zone" data-zone="lightSeismic">';
        html += '<h3>🟡 LIGHT SEISMIC ACTIVITY</h3>';
        html += '<p><strong>Magnitude:</strong> M' + data.earthquake_magnitude.toFixed(2) + '</p>';
        html += '<p><strong>Radius:</strong> ' + data.light_shaking_radius_km.toFixed(2) + ' km</p>';
        html += '<p><strong>Population in zone:</strong> ' + (data.pop_light_seismic - data.pop_moderate_seismic).toLocaleString() + '</p>';
        html += '<p><strong>Intensity:</strong> MMI III-IV</p>';
        html += '<p>Felt indoors by most. Hanging objects swing, slight vibrations, minor disturbances.</p>';
        html += '</div>';

        if (impactData.tsunami && impactData.tsunami.isWater) {
            html += '<div class="impact-zone" data-zone="tsunami">';
            html += '<h3>🌊 TSUNAMI ZONE</h3>';
            html += '<p><strong>Initial Wave Height:</strong> ' + impactData.tsunami.waveHeight.toFixed(2) + ' m</p>';
            html += '<p><strong>Affected Radius:</strong> ' + impactData.tsunami.radius.toFixed(2) + ' km</p>';
            html += '<p>Ocean impact generates massive tsunami. Coastal areas at extreme risk from wave surge.</p>';
            html += '</div>';
        }

        sidebar.innerHTML = html;
        sidebar.addEventListener('scroll', handleSidebarScroll);
        setTimeout(function() { focusZone('crater'); }, 100);
    }

    function getMitigationTactics() {
        if (!currentImpactData) return;

        var btn = document.getElementById('mitigation-btn');
        var contentDiv = document.getElementById('mitigation-content');

        btn.disabled = true;
        contentDiv.innerHTML = '<div class="loading-spinner"></div>';

        var payload = {
            asteroid: currentImpactData.asteroid,
            location: {
                lat: currentImpactData.location.lat,
                lng: currentImpactData.location.lng
            },
            casualty_data: currentImpactData.casualtyData
        };

        fetch('/get_mitigation', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify(payload)
        })
        .then(response => response.json())
        .then(data => {
            btn.disabled = false;
            if (data.error) {
                contentDiv.innerHTML = '<div class="mitigation-content" style="color:#e74c3c;">Error: ' + data.error + '</div>';
            } else {
                contentDiv.innerHTML = '<div class="mitigation-content">' + data.mitigation + '</div>';
            }
        })
        .catch(error => {
            btn.disabled = false;
            contentDiv.innerHTML = '<div class="mitigation-content" style="color:#e74c3c;">Error: ' + error.message + '</div>';
        });
    }

    function handleSidebarScroll() {
        var sidebar = document.getElementById('sidebar-content');
        var zones = sidebar.querySelectorAll('.impact-zone');
        var scrollTop = sidebar.scrollTop;
        var sidebarHeight = sidebar.clientHeight;

        var closestZone = null;
        var minDistance = Infinity;

        zones.forEach(function(zone) {
            var rect = zone.getBoundingClientRect();
            var sidebarRect = sidebar.getBoundingClientRect();
            var zoneTop = rect.top - sidebarRect.top;
            var zoneMiddle = zoneTop + (zone.offsetHeight / 2);
            var viewMiddle = sidebarHeight / 2;
            var distance = Math.abs(zoneMiddle - viewMiddle);

            if (distance < minDistance) {
                minDistance = distance;
                closestZone = zone;
            }
        });

        if (closestZone) {
            var zoneName = closestZone.getAttribute('data-zone');
            if (currentActiveZone !== zoneName) {
                zones.forEach(z => z.classList.remove('active'));
                closestZone.classList.add('active');
                currentActiveZone = zoneName;
                focusZone(zoneName, false);
            }
        }
    }

    function focusZone(zoneName, shouldScroll) {
        if (shouldScroll === undefined) shouldScroll = true;

        if (!impactLayers[zoneName]) return;

        var layerInfo = impactLayers[zoneName];
        var bounds = layerInfo.layer.getBounds();

        map.fitBounds(bounds, {padding: [50, 50], maxZoom: 15});

        if (shouldScroll) {
            var sidebar = document.getElementById('sidebar-content');
            var zones = sidebar.querySelectorAll('.impact-zone');
            zones.forEach(function(z) {
                z.classList.remove('active');
                if (z.getAttribute('data-zone') === zoneName) {
                    z.classList.add('active');
                    z.scrollIntoView({behavior: 'smooth', block: 'center'});
                }
            });
        }

        currentActiveZone = zoneName;
    }

    function resetToAsteroidList() {
        for (var key in impactLayers) {
            if (impactLayers[key].layer) {
                map.removeLayer(impactLayers[key].layer);
            }
            if (impactLayers[key].label) {
                map.removeLayer(impactLayers[key].label);
            }
        }
        impactLayers = {};

        map.setView([20, 0], 2);

        var sidebar = document.getElementById('sidebar-content');
        var html = '<h2>Hazardous Meteor Impacts</h2>';
        html += '<div class="status-text" id="status-text">Found ' + allAsteroids.length + ' hazardous asteroids.</div>';
        html += '<div id="asteroid-list">';

        allAsteroids.forEach(function(ast) {
            var massText = formatMassMT(ast.mass_kg);
            var velocityText = (ast.velocity_kmh !== undefined && ast.velocity_kmh !== null) ? Number(ast.velocity_kmh).toLocaleString(undefined, {maximumFractionDigits: 0}) + ' km/h' : 'N/A';
            html += '<div class="asteroid-item" data-name="' + ast.name + '" data-diameter="' + ast.diameter + '" data-mass="' + ast.mass_kg + '" data-velocity="' + ast.velocity_kmh + '">';
            html += '<div><strong>' + ast.name + '</strong></div>';
            html += '<div>Diameter: ' + ast.diameter.toFixed(2) + ' m</div>';
            html += '<div>Mass: ' + massText + '</div>';
            html += '<div>Velocity: ' + velocityText + '</div>';
            html += '<div>Miss Dist: ' + (ast.miss_distance_km / 1000).toFixed(0) + 'k km</div>';
            html += '<div>Date: ' + ast.date + '</div>';
            html += '<span class="hazard-badge">HAZARDOUS</span></div>';
        });

        html += '</div>';
        html += '<button class="impact-button" id="impact-btn" disabled>SIMULATE IMPACT</button>';
        html += '<div class="info-text">1. Select asteroid<br>2. Click map to place target<br>3. SIMULATE IMPACT</div>';

        sidebar.innerHTML = html;
        sidebar.removeEventListener('scroll', handleSidebarScroll);

        selectedAsteroid = null;
        waypointLocation = null;
        currentActiveZone = null;
        currentImpactData = null;

        reattachAsteroidListEvents();
    }
    </script>
    """

    m.get_root().html.add_child(folium.Element(custom_html))
    return m._repr_html_()


# --- Calculate casualties endpoint ---
@app.route('/calculate_casualties', methods=['POST'])
def calculate_casualties():
    """Calculate impact casualties using GPW v4 population data."""
    data = request.json
    lat = data['lat']
    lon = data['lon']
    diameter = data['diameter']
    mass_kg = data['mass_kg']
    velocity_kmh = data['velocity_kmh']

    casualties = calculate_impact_casualties(lat, lon, diameter, mass_kg, velocity_kmh)
    return jsonify(casualties)


# --- Stream asteroids via SSE ---
@app.route('/stream_asteroids')
def stream_asteroids():
    def generate():
        yield 'data: {"status": "Searching for hazardous asteroids..."}\n\n'
        found_any = False
        for asteroid in generate_asteroids():
            found_any = True
            yield f'data: {json.dumps({"asteroid": asteroid})}\n\n'
        if not found_any:
            yield 'data: {"error":"No asteroids found"}\n\n'
        yield 'data: {"complete": true}\n\n'

    return Response(generate(), mimetype='text/event-stream')


# --- Gemini API Mitigation Endpoint ---
@app.route('/get_mitigation', methods=['POST'])
def get_mitigation():
    try:
        data = request.get_json()

        if not GEMINI_API_KEY:
            print("ERROR: GEMINI_API_KEY is not set!")
            return jsonify({"error": "Gemini API key not configured"}), 500

        print(f"Gemini API Key present: {bool(GEMINI_API_KEY)}")

        asteroid = data.get('asteroid', {})
        location = data.get('location', {})
        casualty_data = data.get('casualty_data', {})

        prompt = f"""You are a disaster response and planetary defense expert. An asteroid impact simulation has been completed with the following parameters:

ASTEROID DETAILS:
- Name: {asteroid.get('name', 'Unknown')}
- Diameter: {asteroid.get('diameter', 0):.2f} meters
- Mass: {asteroid.get('mass_kg', 0) / 1e9:.2f} megatons
- Velocity: {asteroid.get('velocity_kmh', 0):,.0f} km/h
- Impact Energy: {casualty_data.get('impact_energy_joules', 0) / 4.184e12:.2f} kilotons of TNT

IMPACT LOCATION:
- Latitude: {location.get('lat', 0):.4f}
- Longitude: {location.get('lng', 0):.4f}

CASUALTY ESTIMATES (from GPW v4 population data):
- Total Deaths: {casualty_data.get('total_deaths', 0):,}
- Crater Deaths: {casualty_data.get('crater_deaths', 0):,} (100% fatality)
- Shockwave Deaths: {casualty_data.get('shockwave_deaths', 0):,} (30% fatality)
- Strong Seismic Deaths: {casualty_data.get('strong_seismic_deaths', 0):,} (80% fatality)

IMPACT ZONES:
- Crater Diameter: {casualty_data.get('crater_diameter_m', 0):.2f} meters
- Shockwave Radius: {casualty_data.get('shockwave_radius_km', 0):.2f} km
- Strong Seismic Activity (MMI VII+): {casualty_data.get('strong_shaking_radius_km', 0):.2f} km
- Moderate Seismic Activity (MMI V-VI): {casualty_data.get('moderate_shaking_radius_km', 0):.2f} km
- Light Seismic Activity (MMI III-IV): {casualty_data.get('light_shaking_radius_km', 0):.2f} km
- Earthquake Magnitude: M{casualty_data.get('earthquake_magnitude', 0):.2f}

POPULATION IN ZONES:
- Crater Zone: {casualty_data.get('pop_crater', 0):,}
- Shockwave Zone: {casualty_data.get('pop_shockwave', 0):,}
- Strong Seismic Zone: {casualty_data.get('pop_strong_seismic', 0):,}
- Moderate Seismic Zone: {casualty_data.get('pop_moderate_seismic', 0):,}
- Light Seismic Zone: {casualty_data.get('pop_light_seismic', 0):,}

Please provide tactics or things with specifics to mitigate the asteroid from hitting the select area and causing
causalties, and ways to stop the meteor from hitting earth. Also, add if this specific meteor (by name), ever had
or ever will have the chance to hit earth and if so, how much was the chance that it would hit, and if not, tell why.

Be specific, actionable, and prioritize saving lives. Consider the actual casualty estimates and population distributions.
Keep it short and concise. Below 12 sentences pls."""

        gemini_url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={GEMINI_API_KEY}"

        gemini_payload = {
            "contents": [{
                "parts": [{
                    "text": prompt
                }]
            }],
            "generationConfig": {
                "temperature": 0.7,
                "maxOutputTokens": 8192  # Increased from 2048
            }
        }

        response = requests.post(gemini_url, json=gemini_payload, timeout=60)

        print(f"Gemini API Response Status: {response.status_code}")
        print(f"Gemini API Response: {response.text[:500]}")

        if response.status_code != 200:
            error_msg = f"Gemini API error (Status {response.status_code}): {response.text}"
            print(error_msg)
            return jsonify({"error": error_msg}), 500

        result = response.json()
        print(f"Full Gemini Response: {json.dumps(result, indent=2)}")

        if 'candidates' in result and len(result['candidates']) > 0:
            candidate = result['candidates'][0]

            # Check if content exists and has the expected structure
            if 'content' in candidate and 'parts' in candidate['content'] and len(candidate['content']['parts']) > 0:
                mitigation_text = candidate['content']['parts'][0]['text']
                return jsonify({"mitigation": mitigation_text})
            else:
                # Handle case where response was blocked or has different structure
                error_detail = candidate.get('finishReason', 'Unknown reason')
                safety_ratings = candidate.get('safetyRatings', [])
                error_msg = f"Response blocked or incomplete. Reason: {error_detail}. Safety ratings: {safety_ratings}"
                print(error_msg)
                return jsonify({"error": error_msg}), 500
        else:
            return jsonify({"error": f"No valid response from Gemini API. Response: {result}"}), 500

    except Exception as e:
        error_msg = f"Exception in get_mitigation: {str(e)}"
        print(error_msg)
        import traceback
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500


# --- Debug/Test endpoint ---
@app.route('/test_api')
def test_api():
    try:
        test_date = "2024-09-01"
        url = f"https://api.nasa.gov/neo/rest/v1/feed?start_date={test_date}&end_date={test_date}&api_key={NASA_API_KEY}"
        r = requests.get(url, timeout=10)
        return jsonify({"status": "success", "api_key_present": bool(NASA_API_KEY), "response_code": r.status_code,
                        "data_sample": r.json() if r.status_code == 200 else r.text})
    except Exception as e:
        return jsonify({"status": "error", "error": str(e), "api_key_present": bool(NASA_API_KEY)})


# --- Run app ---
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)

import os
from typing import Any

from flask import Flask, jsonify, Response, request
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

dataset = rasterio.open("data/gpw_v4_population_count_rev11_2020_30_sec.tif")

load_dotenv()

app = Flask(__name__)
NASA_API_KEY = os.getenv("NEO_API_KEY")


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

    Args:
        lat: Latitude of center point
        lon: Longitude of center point
        radius_km: Radius in kilometers

    Returns:
        Total population count in the circular area
    """
    try:
        # Convert radius to degrees (approximate)
        # 1 degree latitude ≈ 111 km
        # 1 degree longitude varies by latitude
        lat_degrees = radius_km / 111.0
        lon_degrees = radius_km / (111.0 * math.cos(math.radians(lat)))

        # Define bounding box
        min_lon = lon - lon_degrees
        max_lon = lon + lon_degrees
        min_lat = lat - lat_degrees
        max_lat = lat + lat_degrees

        # Convert geographic coordinates to pixel coordinates
        window = rasterio.windows.from_bounds(
            min_lon, min_lat, max_lon, max_lat,
            dataset.transform
        )

        # Read the data window
        data = dataset.read(1, window=window, masked=True)

        if data.size == 0:
            return 0

        # Get the affine transform for this window
        window_transform = dataset.window_transform(window)

        # Create coordinate arrays for each pixel
        rows, cols = np.meshgrid(
            np.arange(data.shape[0]),
            np.arange(data.shape[1]),
            indexing='ij'
        )

        # Convert pixel coordinates to geographic coordinates
        xs, ys = rasterio.transform.xy(window_transform, rows.flatten(), cols.flatten())
        xs = np.array(xs).reshape(data.shape)
        ys = np.array(ys).reshape(data.shape)

        # Calculate distance from center point for each pixel
        distances = np.sqrt(
            ((xs - lon) * 111.0 * math.cos(math.radians(lat))) ** 2 +
            ((ys - lat) * 111.0) ** 2
        )

        # Create circular mask
        mask = distances <= radius_km

        # Sum population within the circle
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
    Uses the original seismic and impact zone calculations.
    """

    # Calculate impact energy
    velocity_m_s = velocity_kmh * 1000 / 3600
    kinetic_energy = 0.5 * mass_kg * velocity_m_s ** 2  # Joules

    # --- IMPACT ZONES (using original calculations) ---
    # Crater: Complete vaporization
    crater_diameter_m = diameter_m * 15
    crater_radius_km = crater_diameter_m / 2000

    # Shockwave (original calculation)
    shockwave_radius_km = math.pow(kinetic_energy, 1 / 3) * 0.05 / 1000

    # Seismic zones (original calculations)
    magnitude = (2 / 3) * math.log10(kinetic_energy / 1000) - 3.2
    strong_shaking_radius_km = math.pow(10, 0.5 * magnitude - 2.0)
    moderate_shaking_radius_km = math.pow(10, 0.5 * magnitude - 1.3)
    light_shaking_radius_km = math.pow(10, 0.5 * magnitude - 0.8)

    # Tsunami (if water impact)
    rho = 1000  # water density kg/m^3
    g = 9.81
    k = 0.18  # scaling factor
    initial_wave_height = k * math.pow(kinetic_energy / (rho * g), 0.25)
    tsunami_radius_km = 500 * (diameter_m / 1000)

    # --- GET POPULATION IN EACH ZONE ---
    print(f"Calculating casualties for impact at ({lat}, {lon})")
    print(f"Energy: {kinetic_energy:.2e} J, Magnitude: {magnitude:.2f}")

    # Get cumulative populations
    pop_crater = get_population_in_radius(lat, lon, crater_radius_km)
    pop_shockwave = get_population_in_radius(lat, lon, shockwave_radius_km)
    pop_strong_seismic = get_population_in_radius(lat, lon, strong_shaking_radius_km)
    pop_moderate_seismic = get_population_in_radius(lat, lon, moderate_shaking_radius_km)
    pop_light_seismic = get_population_in_radius(lat, lon, light_shaking_radius_km)
    pop_tsunami = get_population_in_radius(lat, lon, tsunami_radius_km)

    # Calculate deaths in each zone (using incremental populations)
    # Crater: 100% fatality
    crater_deaths = int(pop_crater)

    # Shockwave (excluding crater): 30% fatality
    shockwave_deaths = int(max(0, (pop_shockwave - pop_strong_seismic) * 0.3))

    # Strong seismic (excluding shockwave): 60% fatality (MMI VII+)
    strong_seismic_deaths = int(max(0, (pop_strong_seismic - pop_crater) * 0.8))

    # Moderate and light seismic: no deaths calculated (injuries only)
    moderate_seismic_deaths = 0
    light_seismic_deaths = 0

    # Tsunami deaths: not calculated (water detection needs improvement)
    tsunami_deaths = 0

    total_deaths = (crater_deaths + shockwave_deaths + strong_seismic_deaths +
                    moderate_seismic_deaths + light_seismic_deaths)

    print(f"Total deaths: {total_deaths:,}")

    return {
        "total_deaths": total_deaths,
        "crater_deaths": crater_deaths,
        "shockwave_deaths": shockwave_deaths,
        "strong_seismic_deaths": strong_seismic_deaths,
        "moderate_seismic_deaths": moderate_seismic_deaths,
        "light_seismic_deaths": light_seismic_deaths,
        "tsunami_deaths": tsunami_deaths,
        "crater_radius_km": round(crater_radius_km, 3),
        "crater_diameter_m": round(crater_diameter_m, 2),
        "shockwave_radius_km": round(shockwave_radius_km, 2),
        "strong_shaking_radius_km": round(strong_shaking_radius_km, 2),
        "moderate_shaking_radius_km": round(moderate_shaking_radius_km, 2),
        "light_shaking_radius_km": round(light_shaking_radius_km, 2),
        "tsunami_wave_height_m": round(initial_wave_height, 2),
        "tsunami_radius_km": round(tsunami_radius_km, 2),
        "impact_energy_joules": kinetic_energy,
        "earthquake_magnitude": round(magnitude, 2)
    }


def asteroid_energy(mass_kg, velocity_kmh):
    """Calculate kinetic energy of an asteroid in Joules."""
    velocity_m_s = velocity_kmh / 3.6
    energy_joules = 0.5 * mass_kg * velocity_m_s ** 2
    return energy_joules


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


# --- Index route with folium map ---
@app.route('/')
def index():
    m = folium.Map(location=[20, 0], zoom_start=2)

    custom_html = """
    <style>
    .sidebar {position: fixed; left: 0; top: 0; width: 350px; height: 100%; background-color: #2c3e50; color: white; padding: 20px; overflow-y: auto; z-index:1000;}
    .asteroid-item {background-color:#34495e; margin:10px 0; padding:15px; border-radius:5px; cursor:pointer; transition:0.3s;}
    .asteroid-item:hover {background-color:#e74c3c; transform: translateX(5px);}
    .asteroid-item.selected {background-color:#e74c3c; border:2px solid white;}
    .hazard-badge {display:inline-block;background-color:#e74c3c;color:white;padding:2px 6px;border-radius:3px;font-size:10px;margin-top:5px;}
    .impact-button {width:100%; padding:15px; background-color:#27ae60; color:white; border:none; border-radius:5px; font-weight:bold; cursor:pointer; margin-top:20px;}
    .impact-button:disabled {background-color:#95a5a6; cursor:not-allowed;}
    .death-toll {background-color:#c0392b; padding:15px; border-radius:5px; margin-top:15px; display:none;}
    .death-toll h3 {margin:0 0 10px 0; font-size:16px;}
    .death-stat {font-size:11px; margin:5px 0; padding:5px; background-color:#922b21; border-radius:3px;}
    #map {margin-left:350px;}
    </style>

    <div class="sidebar">
        <h2>☄️ Meteor Impact Simulator</h2>
        <div class="status-text" id="status-text">Searching for asteroids...</div>
        <div id="asteroid-list"></div>
        <button class="impact-button" id="impact-btn" disabled>SIMULATE IMPACT</button>
        <div class="info-text" style="font-size:11px; color:#bdc3c7; margin-top:10px;">1. Select asteroid<br>2. Click map to place target<br>3. SIMULATE IMPACT</div>
        <div class="death-toll" id="death-toll">
            <h3>💀 CASUALTY ESTIMATE</h3>
            <div id="death-stats">Calculating...</div>
        </div>
    </div>

    <script>
    var selectedAsteroid=null, waypointMarker=null, waypointLocation=null, map=null, allAsteroids=[];

    document.getElementById('asteroid-list').addEventListener('click', function(e){
        var item=e.target.closest('.asteroid-item'); if(!item) return;
        document.querySelectorAll('.asteroid-item').forEach(i=>i.classList.remove('selected'));
        item.classList.add('selected');
        selectedAsteroid={
            name:item.getAttribute('data-name'),
            diameter:parseFloat(item.getAttribute('data-diameter')),
            mass_kg:parseFloat(item.getAttribute('data-mass')),
            velocity_kmh:parseFloat(item.getAttribute('data-velocity'))
        };
        updateImpactButton();
    });

    function addAsteroid(ast){
        allAsteroids.push(ast);
        var massText=(ast.mass_kg!==undefined)?Number(ast.mass_kg).toExponential(2)+' kg':'N/A';
        var velocityText=(ast.velocity_kmh!==undefined)?Number(ast.velocity_kmh).toFixed(0)+' km/h':'N/A';
        var html='<div class="asteroid-item" data-name="'+ast.name+'" data-diameter="'+ast.diameter+'" data-mass="'+ast.mass_kg+'" data-velocity="'+ast.velocity_kmh+'">'+
                 '<div><strong>'+ast.name+'</strong></div>'+
                 '<div style="font-size:11px;">Ø: '+ast.diameter.toFixed(1)+' m | Mass: '+massText+'</div>'+
                 '<div style="font-size:11px;">Velocity: '+velocityText+'</div>'+
                 '<span class="hazard-badge">HAZARDOUS</span></div>';
        document.getElementById('asteroid-list').innerHTML+=html;
        document.getElementById('status-text').innerHTML='Found '+allAsteroids.length+' asteroid(s)...';
    }

    function updateImpactButton(){
        document.getElementById('impact-btn').disabled=!(selectedAsteroid && waypointLocation);
    }

    function is_water(lat, lng) {
        // Simplified water detection - checks if likely ocean
        // More accurate would use a water mask dataset
        return (Math.abs(lat) < 60); // rough ocean probability
    }

    setTimeout(function(){
        for(var key in window){if(window[key] instanceof L.Map){map=window[key];
            map.on('click', function(e){
                if(waypointMarker){map.removeLayer(waypointMarker);}
                waypointLocation=e.latlng;
                waypointMarker=L.marker(e.latlng).addTo(map);
                waypointMarker.bindPopup('🎯 Impact Target<br>Lat:'+e.latlng.lat.toFixed(4)+'<br>Lng:'+e.latlng.lng.toFixed(4)).openPopup();
                updateImpactButton();
            }); break;}
        }
    }, 1000);

    document.getElementById('impact-btn').addEventListener('click', function() {
        if (!selectedAsteroid || !waypointLocation) return;

        // Show death toll panel
        document.getElementById('death-toll').style.display = 'block';
        document.getElementById('death-stats').innerHTML = 'Calculating casualties from GPW v4 data...';

        // Call backend to calculate casualties
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
        .then(data => {
            // Display casualties
            var statsHtml = 
                '<div class="death-stat"><strong>TOTAL DEATHS: ' + data.total_deaths.toLocaleString() + '</strong></div>' +
                '<div class="death-stat">☠️ Crater: ' + data.crater_deaths.toLocaleString() + ' (100%)</div>' +
                '<div class="death-stat">💨 Shockwave: ' + data.shockwave_deaths.toLocaleString() + ' (30%)</div>' +
                '<div class="death-stat">🔴 Strong Seismic: ' + data.strong_seismic_deaths.toLocaleString() + ' (60%)</div>' +
                '<div class="death-stat">🟠 Moderate Seismic: Injuries only</div>' +
                '<div class="death-stat">🟡 Light Seismic: Minor damage</div>';

            if (data.tsunami_deaths > 0) {
                statsHtml += '<div class="death-stat">🌊 Tsunami: Data unavailable</div>';
            }

            statsHtml += '<div class="death-stat" style="margin-top:8px;">⚡ Energy: ' + data.impact_energy_joules.toExponential(2) + ' J</div>' +
                '<div class="death-stat">🌍 Earthquake: M' + data.earthquake_magnitude + '</div>';

            document.getElementById('death-stats').innerHTML = statsHtml;

            // LAYER ORDER: Light Seismic -> Tsunami -> Moderate -> Shockwave -> Strong -> Crater

            // 1. LIGHT SEISMIC (bottom layer)
            var lightSeismic = L.circle(waypointLocation, {
                radius: data.light_shaking_radius_km * 1000,
                color: '#e67e22', fillColor: '#e67e22', fillOpacity: 0.12, weight: 1, dashArray: '5, 5', interactive: true
            }).addTo(map);
            lightSeismic.bindPopup(
                '<strong>🟡 LIGHT SEISMIC ACTIVITY</strong><br>' +
                'Magnitude: M' + data.earthquake_magnitude.toFixed(2) + '<br>' +
                'Radius: ' + data.light_shaking_radius_km.toFixed(2) + ' km<br>' +
                'Deaths: Minor damage only<br>' +
                'Intensity: MMI III-IV<br>' +
                'Felt indoors, hanging objects swing, slight vibration'
            );

            // 2. TSUNAMI (if applicable)
            if (is_water(waypointLocation.lat, waypointLocation.lng)) {
                var tsunamiCircle = L.circle(waypointLocation, {
                    radius: data.tsunami_radius_km * 1000,
                    color: '#3498db', fillColor: '#3498db', fillOpacity: 0.15, weight: 2, dashArray: '10, 10', interactive: true
                }).addTo(map);
                tsunamiCircle.bindPopup(
                    '<strong>🌊 TSUNAMI ZONE</strong><br>' +
                    'Initial Wave Height: ' + data.tsunami_wave_height_m.toFixed(2) + ' m<br>' +
                    'Affected Radius: ' + data.tsunami_radius_km.toFixed(2) + ' km<br>' +
                    'Deaths: Calculation unavailable<br>' +
                    '(Impact in ocean)'
                );
            }

            // 3. MODERATE SEISMIC
            var moderateSeismic = L.circle(waypointLocation, {
                radius: data.moderate_shaking_radius_km * 1000,
                color: '#d35400', fillColor: '#d35400', fillOpacity: 0.18, weight: 2, interactive: true
            }).addTo(map);
            moderateSeismic.bindPopup(
                '<strong>🟠 MODERATE SEISMIC ACTIVITY</strong><br>' +
                'Magnitude: M' + data.earthquake_magnitude.toFixed(2) + '<br>' +
                'Radius: ' + data.moderate_shaking_radius_km.toFixed(2) + ' km<br>' +
                'Deaths: Injuries, not fatal<br>' +
                'Intensity: MMI V-VI<br>' +
                'Felt by all, furniture moves, weak structures damaged'
            );

            // 4. SHOCKWAVE
            var shockwave = L.circle(waypointLocation, {
                radius: data.shockwave_radius_km * 1000,
                color: '#f1c40f', fillColor: '#f1c40f', fillOpacity: 0.2, weight: 2, interactive: true
            }).addTo(map);
            shockwave.bindPopup(
                '<strong>💨 SHOCKWAVE ZONE</strong><br>' + 
                'Radius: ' + data.shockwave_radius_km.toFixed(2) + ' km<br>' +
                'Deaths: ' + data.shockwave_deaths.toLocaleString() + '<br>' +
                'Extreme destruction and fires<br>' +
                'Asteroid Mass: ' + selectedAsteroid.mass_kg.toExponential(2) + ' kg<br>' +
                'Velocity: ' + selectedAsteroid.velocity_kmh.toFixed(0) + ' km/h'
            );

            // 5. STRONG SEISMIC
            var strongSeismic = L.circle(waypointLocation, {
                radius: data.strong_shaking_radius_km * 1000,
                color: '#c0392b', fillColor: '#c0392b', fillOpacity: 0.25, weight: 2, interactive: true
            }).addTo(map);
            strongSeismic.bindPopup(
                '<strong>🔴 STRONG SEISMIC ACTIVITY</strong><br>' +
                'Magnitude: M' + data.earthquake_magnitude.toFixed(2) + '<br>' +
                'Radius: ' + data.strong_shaking_radius_km.toFixed(2) + ' km<br>' +
                'Deaths: ' + data.strong_seismic_deaths.toLocaleString() + '<br>' +
                'Intensity: MMI VII+<br>' +
                'Significant damage, buildings collapse, ground cracks'
            );

            // 6. CRATER (top layer)
            var crater = L.circle(waypointLocation, {
                radius: data.crater_diameter_m / 2,
                color: 'black', fillColor: '#000000', fillOpacity: 1, weight: 3, interactive: true
            }).addTo(map);
            crater.bindPopup(
                '<strong>💥 IMPACT CRATER</strong><br>' +
                'Asteroid: ' + selectedAsteroid.name + '<br>' +
                'Asteroid Diameter: ' + selectedAsteroid.diameter.toFixed(2) + ' m<br>' +
                'Crater Diameter: ' + data.crater_diameter_m.toFixed(2) + ' m<br>' +
                'Deaths: ' + data.crater_deaths.toLocaleString() + '<br>' +
                'Impact Energy: ' + data.impact_energy_joules.toExponential(2) + ' J<br>' +
                'Lat: ' + waypointLocation.lat.toFixed(4) + '<br>' +
                'Lng: ' + waypointLocation.lng.toFixed(4)
            ).openPopup();

            if (waypointMarker) {
                map.removeLayer(waypointMarker);
                waypointMarker = null;
                waypointLocation = null;
            }
            document.getElementById('impact-btn').disabled = true;
        })
        .catch(err => {
            document.getElementById('death-stats').innerHTML = 'Error calculating casualties: ' + err;
        });
    });

    fetch('/stream_asteroids').then(r=>{
        const reader=r.body.getReader(); const decoder=new TextDecoder(); let buffer='';
        function processText(result){
            if(result.done){document.getElementById('status-text').innerHTML='Found '+allAsteroids.length+' hazardous asteroids'; return;}
            buffer+=decoder.decode(result.value,{stream:true});
            const lines=buffer.split('\\n'); buffer=lines.pop();
            lines.forEach(line=>{
                if(line.startsWith('data: ')){try{const data=JSON.parse(line.substring(6));
                    if(data.asteroid){addAsteroid(data.asteroid);}
                }catch(e){}}});
            return reader.read().then(processText);
        }
        reader.read().then(processText);
    });
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

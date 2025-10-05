import os
from typing import Any

from flask import Flask, jsonify, Response
import folium
import requests
from dotenv import load_dotenv
import datetime
import json
import time
import math
import rasterio
import numpy as np
from shapely.geometry import Point
from shapely.geometry.polygon import Polygon
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


def asteroid_energy(mass_kg, velocity_kmh):
    """
    Calculate kinetic energy of an asteroid in Joules.
    """
    velocity_m_s = velocity_kmh / 3.6  # convert km/h to m/s
    energy_joules = 0.5 * mass_kg * velocity_m_s ** 2
    return energy_joules


def tsunami_size(mass_kg, velocity_kmh, diameter_m):
    """Calculate wave height and tsunami radius"""
    energy_joules = asteroid_energy(mass_kg, velocity_kmh)
    initial_wave_height = 0.2 * (energy_joules / 9810) ** 0.25
    diameter_km = diameter_m / 1000
    tsunami_radius = 500 * diameter_km
    return initial_wave_height, tsunami_radius


def earthquake_size(mass_kg, velocity_kmh):
    """Calculate earthquake magnitude and radius"""
    energy_joules = asteroid_energy(mass_kg, velocity_kmh)
    seismic_energy = (energy_joules)/1000
    magnitude = (2 / 3) * math.log10(seismic_energy) - 3.2
    radius_km = 10 ** (magnitude - 3) / 1.5
    return radius_km, magnitude


# --- Asteroid generator ---
def generate_asteroids():
    #fetches  hazardous asteroids from NEO
    count = 0
    start_time = time.time()
    timeout = 60  # seconds

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

    # Inline HTML + JS for sidebar, asteroid list, and impact simulation
    custom_html = """
    <style>
    .sidebar {position: fixed; left: 0; top: 0; width: 350px; height: 100%; background-color: #2c3e50; color: white; padding: 20px; overflow-y: auto; z-index:1000;}
    .asteroid-item {background-color:#34495e; margin:10px 0; padding:15px; border-radius:5px; cursor:pointer; transition:0.3s;}
    .asteroid-item:hover {background-color:#e74c3c; transform: translateX(5px);}
    .asteroid-item.selected {background-color:#e74c3c; border:2px solid white;}
    .hazard-badge {display:inline-block;background-color:#e74c3c;color:white;padding:2px 6px;border-radius:3px;font-size:10px;margin-top:5px;}
    .impact-button {width:100%; padding:15px; background-color:#27ae60; color:white; border:none; border-radius:5px; font-weight:bold; cursor:pointer; margin-top:20px;}
    .impact-button:disabled {background-color:#95a5a6; cursor:not-allowed;}
    #map {margin-left:350px;}
    </style>

    <div class="sidebar">
        <h2>Hazardous Meteor Impacts</h2>
        <div class="status-text" id="status-text">Searching for asteroids...</div>
        <div id="asteroid-list"></div>
        <button class="impact-button" id="impact-btn" disabled>SIMULATE IMPACT</button>
        <div class="info-text">1. Select asteroid<br>2. Click map to place target<br>3. SIMULATE IMPACT</div>
    </div>

    <script>
    var selectedAsteroid=null, waypointMarker=null, waypointLocation=null, map=null, allAsteroids=[];

    document.getElementById('asteroid-list').addEventListener('click', function(e){
        var item=e.target.closest('.asteroid-item'); if(!item) return;
        document.querySelectorAll('.asteroid-item').forEach(i=>i.classList.remove('selected'));
        item.classList.add('selected');
        var name=item.getAttribute('data-name');
        var diameter=parseFloat(item.getAttribute('data-diameter'));
        var mass=parseFloat(item.getAttribute('data-mass'));
        var velocity=parseFloat(item.getAttribute('data-velocity'));
        selectedAsteroid={name:name, diameter:diameter, mass_kg:mass, velocity_kmh:velocity};
        updateImpactButton();
    });

    function addAsteroid(ast){
        allAsteroids.push(ast);
        var massText=(ast.mass_kg!==undefined && ast.mass_kg!==null)?Number(ast.mass_kg).toExponential(2)+' kg':'N/A';
        var velocityText=(ast.velocity_kmh!==undefined && ast.velocity_kmh!==null)?Number(ast.velocity_kmh).toFixed(0)+' km/h':'N/A';
        var html='<div class="asteroid-item" data-name="'+ast.name+'" data-diameter="'+ast.diameter+'" data-mass="'+ast.mass_kg+'" data-velocity="'+ast.velocity_kmh+'">'+
                 '<div><strong>'+ast.name+'</strong></div>'+
                 '<div>Diameter: '+ast.diameter.toFixed(2)+' m</div>'+
                 '<div>Mass: '+massText+'</div>'+
                 '<div>Velocity: '+velocityText+'</div>'+
                 '<div>Miss Dist: '+(ast.miss_distance_km/1000).toFixed(0)+'k km</div>'+
                 '<div>Date: '+ast.date+'</div>'+
                 '<span class="hazard-badge">HAZARDOUS</span></div>';
        document.getElementById('asteroid-list').innerHTML+=html;
        document.getElementById('status-text').innerHTML='Found '+allAsteroids.length+' asteroid(s)... searching...';
    }

    function updateImpactButton(){document.getElementById('impact-btn').disabled=!(selectedAsteroid && waypointLocation);}

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

    document.getElementById('impact-btn').addEventListener('click', function() {
        if (!selectedAsteroid || !waypointLocation) return;

        var velocity_kmh = selectedAsteroid.velocity_kmh;
        var mass_kg = selectedAsteroid.mass_kg;
        var diameter_m = selectedAsteroid.diameter;
        var velocity_m_s = velocity_kmh * 1000 / 3600;
        var kineticEnergy = 0.5 * mass_kg * velocity_m_s * velocity_m_s; // Joules

        // --- TSUNAMI (only if water) ---
        function is_water(lat, lng) {
            // Simplified water detection - checks if likely ocean
            return (lat < 60 && lat > -60); // rough "mostly ocean" simplification
        }

        if (is_water(waypointLocation.lat, waypointLocation.lng)) {
            var rho = 1000; // water density kg/m^3
            var g = 9.81;
            var k = 0.18; // scaling factor
            var initial_wave_height = k * Math.pow(kineticEnergy / (rho * g), 0.25);
            var tsunami_radius_km = 500 * (diameter_m / 1000);

            var tsunamiCircle = L.circle(waypointLocation, {
                radius: tsunami_radius_km * 1000,
                color: '#3498db',
                fillColor: '#3498db',
                fillOpacity: 0.15,
                weight: 2,
                dashArray: '10, 10',
                interactive: true
            }).addTo(map);

            tsunamiCircle.bindPopup(
                '<strong>TSUNAMI ZONE</strong><br>' +
                'Initial Wave Height: ' + initial_wave_height.toFixed(2) + ' m<br>' +
                'Affected Radius: ' + tsunami_radius_km.toFixed(2) + ' km<br>' +
                '(Impact in ocean)'
            );
        }

        // --- SEISMIC ACTIVITY ZONES ---
        var magnitude = (2/3) * Math.log10(kineticEnergy/1000) - 3.2;
        var strongShakingRadius = Math.pow(10, 0.5 * magnitude - 2.0);
        var lightShakingRadius = Math.pow(10, 0.5 * magnitude - 0.8);
        var moderateShakingRadius = Math.pow(10, 0.5 * magnitude - 1.3);

        // Perceptible seismic zone removed as per requirements

        var lightSeismic = L.circle(waypointLocation, {
            radius: lightShakingRadius * 1000,
            color: '#e67e22',
            fillColor: '#e67e22',
            fillOpacity: 0.12,
            weight: 1,
            dashArray: '5, 5',
            interactive: true
        }).addTo(map);
        lightSeismic.bindPopup(
            '<strong>LIGHT SEISMIC ACTIVITY</strong><br>' +
            'Magnitude: ' + magnitude.toFixed(2) + '<br>' +
            'Radius: ' + lightShakingRadius.toFixed(2) + ' km<br>' +
            'Intensity: MMI III-IV<br>' +
            'Felt indoors, hanging objects swing, slight vibration'
        );

        var moderateSeismic = L.circle(waypointLocation, {
            radius: moderateShakingRadius * 1000,
            color: '#d35400',
            fillColor: '#d35400',
            fillOpacity: 0.18,
            weight: 2,
            interactive: true
        }).addTo(map);
        moderateSeismic.bindPopup(
            '<strong>MODERATE SEISMIC ACTIVITY</strong><br>' +
            'Magnitude: ' + magnitude.toFixed(2) + '<br>' +
            'Radius: ' + moderateShakingRadius.toFixed(2) + ' km<br>' +
            'Intensity: MMI V-VI<br>' +
            'Felt by all, furniture moves, weak structures damaged'
        );

        var strongSeismic = L.circle(waypointLocation, {
            radius: strongShakingRadius * 1000,
            color: '#c0392b',
            fillColor: '#c0392b',
            fillOpacity: 0.25,
            weight: 2,
            interactive: true
        }).addTo(map);
        strongSeismic.bindPopup(
            '<strong>STRONG SEISMIC ACTIVITY</strong><br>' +
            'Magnitude: ' + magnitude.toFixed(2) + '<br>' +
            'Radius: ' + strongShakingRadius.toFixed(2) + ' km<br>' +
            'Intensity: MMI VII+<br>' +
            'Significant damage, buildings collapse, ground cracks'
        );

        // --- SHOCKWAVE ---
        var shockwaveRadius = Math.pow(kineticEnergy, 1/3) * 0.05;
        var shockwave = L.circle(waypointLocation, {
            radius: shockwaveRadius,
            color: '#f1c40f',
            fillColor: '#f1c40f',
            fillOpacity: 0.2,
            weight: 2,
            interactive: true
        }).addTo(map);
        shockwave.bindPopup(
            '<strong>SHOCKWAVE ZONE</strong><br>' + 
            'Radius: ' + (shockwaveRadius / 1000).toFixed(2) + ' km<br>' +
            'Extreme destruction and fires<br>' +
            'Asteroid Mass: ' + mass_kg.toExponential(2) + ' kg<br>' +
            'Velocity: ' + velocity_kmh.toFixed(0) + ' km/h'
        );

        // --- CRATER ---
        var craterDiameter = diameter_m * 15;
        var crater = L.circle(waypointLocation, {
            radius: craterDiameter,
            color: 'black',
            fillColor: '#000000',
            fillOpacity: 1,
            weight: 3,
            interactive: true
        }).addTo(map);
        crater.bindPopup(
            '<strong>IMPACT CRATER</strong><br>' +
            'Asteroid: ' + selectedAsteroid.name + '<br>' +
            'Asteroid Diameter: ' + diameter_m.toFixed(2) + ' m<br>' +
            'Crater Diameter: ' + craterDiameter.toFixed(2) + ' m<br>' +
            'Impact Energy: ' + kineticEnergy.toExponential(2) + ' J<br>' +
            'Lat: ' + waypointLocation.lat.toFixed(4) + '<br>' +
            'Lng: ' + waypointLocation.lng.toFixed(4)
        ).openPopup();

        if (waypointMarker) {
            map.removeLayer(waypointMarker);
            waypointMarker = null;
            waypointLocation = null;
        }
        document.getElementById('impact-btn').disabled = true;
    });

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
    </script>
    """

    m.get_root().html.add_child(folium.Element(custom_html))
    return m._repr_html_()


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

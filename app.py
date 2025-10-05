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


def impact_wind_speed(energy_joules, distance_m=1000):
    """
    Estimate wind speed (m/s) from asteroid impact blast using simplified physics.
    - energy_joules: impact kinetic energy (J)
    - distance_m: distance from impact center (m)
    """
    RHO_AIR = 1.225  # kg/m^3
    C_SOUND = 343.0  # m/s
    # TNT equivalent (1 ton TNT = 4.184e9 J)
    W_tnt_kg = energy_joules / 4.184e6
    # Scaled distance Z = R / W^(1/3)
    Z = distance_m / (W_tnt_kg ** (1 / 3))
    # Approximate overpressure (Pa) based on empirical fit to Kingery–Bulmash curve
    # Only a rough fit for demonstration purposes
    if Z <= 0:
        Z = 0.1
    delta_p = 1e5 * (1 / (Z ** 1.8))  # Pa, approximate scaling
    # Particle velocity (air movement speed)
    u = delta_p / (RHO_AIR * C_SOUND)
    return max(u, 0)


def tsunami_size(mass_kg, velocity_kmh, diameter_m):
    """Calculate wave height and tsunami radius"""
    energy_joules = asteroid_energy(mass_kg, velocity_kmh)
    initial_wave_height = 0.2 * (energy_joules / 9810) ** 0.25
    diameter_km = diameter_m / 1000
    tsunami_radius = 500 * diameter_km
    return initial_wave_height, tsunami_radius


def earthquake_size(mass_kg, velocity_kmh):
    """Calculate earthquake magnitude and radius with corrected energy scaling"""
    energy_joules = asteroid_energy(mass_kg, velocity_kmh)
    seismic_energy = energy_joules / 1000  # More accurate seismic energy conversion
    magnitude = (2 / 3) * math.log10(seismic_energy) - 3.2
    radius_km = 10 ** (magnitude - 3) / 1.5
    return radius_km, magnitude


# --- Asteroid generator ---
def generate_asteroids():
    """Generator yielding hazardous asteroids with mass."""
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
    .sidebar {position: fixed; left: 0; top: 0; width: 350px; height: 100%; background-color: #2c3e50; color: white; padding: 20px; padding-bottom: 300px; overflow-y: auto; z-index:1000;}
    .asteroid-item {background-color:#34495e; margin:10px 0; padding:15px; border-radius:5px; cursor:pointer; transition:0.3s;}
    .asteroid-item:hover {background-color:#e74c3c; transform: translateX(5px);}
    .asteroid-item.selected {background-color:#e74c3c; border:2px solid white;}
    .hazard-badge {display:inline-block;background-color:#e74c3c;color:white;padding:2px 6px;border-radius:3px;font-size:10px;margin-top:5px;}
    .impact-button {width:100%; padding:15px; background-color:#27ae60; color:white; border:none; border-radius:5px; font-weight:bold; cursor:pointer; margin-top:20px;}
    .impact-button:disabled {background-color:#95a5a6; cursor:not-allowed;}
    .impact-zone {background-color:#34495e; margin:20px 0; padding:20px; border-radius:8px; border-left:4px solid #e74c3c; cursor:pointer; transition:0.3s;}
    .impact-zone:hover {background-color:#415b76; transform: translateX(3px);}
    .impact-zone.active {background-color:#e74c3c; border-left-color:#fff;}
    .impact-zone h3 {margin-top:0; font-size:18px; color:#ecf0f1;}
    .impact-zone p {margin:8px 0; font-size:14px; line-height:1.6;}
    .back-button {width:100%; padding:12px; background-color:#7f8c8d; color:white; border:none; border-radius:5px; font-weight:bold; cursor:pointer; margin-bottom:20px;}
    .back-button:hover {background-color:#95a5a6;}
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
    var impactLayers = {}; // Store all impact layers
    var currentActiveZone = null;

    function formatMassMT(mass_kg) {
        if (mass_kg === undefined || mass_kg === null) return 'N/A';
        // Correctly convert kg to Megatons (1 billion kg)
        return (Number(mass_kg) / 1e9).toLocaleString(undefined, {maximumFractionDigits: 2}) + ' MT';
    }

    function formatEnergyTNT(energy_joules) {
        if (energy_joules === undefined || energy_joules === null) return 'N/A';
        // 1 kiloton of TNT = 4.184e12 Joules
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

            var velocity_kmh = selectedAsteroid.velocity_kmh;
            var mass_kg = selectedAsteroid.mass_kg;
            var diameter_m = selectedAsteroid.diameter;
            var velocity_m_s = velocity_kmh * 1000 / 3600;
            var kineticEnergy = 0.5 * mass_kg * velocity_m_s * velocity_m_s; // Energy in Joules

            impactLayers = {};

            var craterDiameter = diameter_m * 20;
            var crater = L.circle(waypointLocation, {
                radius: craterDiameter,
                color: 'black',
                fillColor: '#000000',
                fillOpacity: 1,
                weight: 3,
                interactive: false
            }).addTo(map);

            var craterLabel = L.marker(waypointLocation, {
                icon: L.divIcon({
                    className: 'impact-label',
                    html: '<div style="background:rgba(0,0,0,0.8);color:white;padding:5px;border-radius:3px;font-size:11px;font-weight:bold;white-space:nowrap;">Crater: ' + (craterDiameter).toFixed(0) + ' m</div>',
                    iconSize: [100, 20]
                }),
                interactive: false
            }).addTo(map);

            impactLayers.crater = {layer: crater, center: waypointLocation, radius: craterDiameter, label: craterLabel};

            var shockwaveRadius = Math.pow(kineticEnergy, 1/3) * 0.05;
            var shockwave = L.circle(waypointLocation, {
                radius: shockwaveRadius,
                color: '#f1c40f',
                fillColor: '#f1c40f',
                fillOpacity: 0.2,
                weight: 2,
                interactive: false
            }).addTo(map);

            var shockwaveLabel = L.marker([waypointLocation.lat, waypointLocation.lng], {
                icon: L.divIcon({
                    className: 'impact-label',
                    html: '<div style="background:rgba(241,196,15,0.9);color:black;padding:5px;border-radius:3px;font-size:11px;font-weight:bold;white-space:nowrap;">Shockwave: ' + (shockwaveRadius/1000).toFixed(1) + ' km</div>',
                    iconSize: [120, 20]
                }),
                interactive: false
            }).addTo(map);

            impactLayers.shockwave = {layer: shockwave, center: waypointLocation, radius: shockwaveRadius, label: shockwaveLabel};

            var RHO_AIR = 1.225;
            var C_SOUND = 343.0;
            var W_tnt_kg = kineticEnergy / 4.184e6;
            var distance_ref_m = 1000;
            var Z = distance_ref_m / Math.pow(W_tnt_kg, 1/3);
            if (Z <= 0) Z = 0.1;
            var delta_p = 1e5 * (1 / Math.pow(Z, 1.8));
            var wind_speed_ms = delta_p / (RHO_AIR * C_SOUND);
            var wind_speed_kmh = wind_speed_ms * 3.6;

            var target_wind_kmh = 60;
            var target_wind_ms = target_wind_kmh / 3.6;
            var u_ref_ms = wind_speed_ms;
            var Z_ref = Z;
            var Z_target = Math.pow(u_ref_ms / target_wind_ms, 1 / 1.8) * Z_ref;
            var R_target = Z_target * Math.pow(W_tnt_kg, 1/3);

            var windZone = L.circle(waypointLocation, {
                radius: R_target,
                color: '#5dade2',
                fillColor: '#5dade2',
                fillOpacity: 0.15,
                weight: 2,
                interactive: false
            }).addTo(map);

            var windLabel = L.marker([waypointLocation.lat, waypointLocation.lng], {
                icon: L.divIcon({
                    className: 'impact-label',
                    html: '<div style="background:rgba(93,173,226,0.9);color:white;padding:5px;border-radius:3px;font-size:11px;font-weight:bold;white-space:nowrap;">Wind Zone: ' + (R_target/1000).toFixed(1) + ' km</div>',
                    iconSize: [120, 20]
                }),
                interactive: false
            }).addTo(map);

            impactLayers.wind = {layer: windZone, center: waypointLocation, radius: R_target, label: windLabel};

            var seismic_energy = kineticEnergy / 1000;
            var magnitude = (2/3) * Math.log10(seismic_energy) - 3.2;
            var strongShakingRadius = Math.pow(10, 0.5 * magnitude - 2.0);
            var lightShakingRadius = Math.pow(10, 0.5 * magnitude - 0.8);
            var moderateShakingRadius = Math.pow(10, 0.5 * magnitude - 1.3);

            var lightSeismic = L.circle(waypointLocation, {
                radius: lightShakingRadius * 1000,
                color: '#e67e22',
                fillColor: '#e67e22',
                fillOpacity: 0.12,
                weight: 1,
                dashArray: '5, 5',
                interactive: false
            }).addTo(map);

            var lightSeismicLabel = L.marker([waypointLocation.lat, waypointLocation.lng], {
                icon: L.divIcon({
                    className: 'impact-label',
                    html: '<div style="background:rgba(230,126,34,0.9);color:white;padding:5px;border-radius:3px;font-size:11px;font-weight:bold;white-space:nowrap;">Light Seismic: ' + lightShakingRadius.toFixed(1) + ' km</div>',
                    iconSize: [140, 20]
                }),
                interactive: false
            }).addTo(map);

            impactLayers.lightSeismic = {layer: lightSeismic, center: waypointLocation, radius: lightShakingRadius * 1000, label: lightSeismicLabel};

            var moderateSeismic = L.circle(waypointLocation, {
                radius: moderateShakingRadius * 1000,
                color: '#d35400',
                fillColor: '#d35400',
                fillOpacity: 0.18,
                weight: 2,
                interactive: false
            }).addTo(map);

            var moderateSeismicLabel = L.marker([waypointLocation.lat, waypointLocation.lng], {
                icon: L.divIcon({
                    className: 'impact-label',
                    html: '<div style="background:rgba(211,84,0,0.9);color:white;padding:5px;border-radius:3px;font-size:11px;font-weight:bold;white-space:nowrap;">Moderate Seismic: ' + moderateShakingRadius.toFixed(1) + ' km</div>',
                    iconSize: [160, 20]
                }),
                interactive: false
            }).addTo(map);

            impactLayers.moderateSeismic = {layer: moderateSeismic, center: waypointLocation, radius: moderateShakingRadius * 1000, label: moderateSeismicLabel};

            var strongSeismic = L.circle(waypointLocation, {
                radius: strongShakingRadius * 1000,
                color: '#c0392b',
                fillColor: '#c0392b',
                fillOpacity: 0.25,
                weight: 2,
                interactive: false
            }).addTo(map);

            var strongSeismicLabel = L.marker([waypointLocation.lat, waypointLocation.lng], {
                icon: L.divIcon({
                    className: 'impact-label',
                    html: '<div style="background:rgba(192,57,43,0.9);color:white;padding:5px;border-radius:3px;font-size:11px;font-weight:bold;white-space:nowrap;">Strong Seismic: ' + strongShakingRadius.toFixed(1) + ' km</div>',
                    iconSize: [150, 20]
                }),
                interactive: false
            }).addTo(map);

            impactLayers.strongSeismic = {layer: strongSeismic, center: waypointLocation, radius: strongShakingRadius * 1000, label: strongSeismicLabel};

            // Water detection - returns true for major water bodies
            function is_water(lat, lng) {
                // Normalize longitude to -180 to 180
                while (lng > 180) lng -= 360;
                while (lng < -180) lng += 360;

                // Arctic and Antarctic Oceans
                if (lat > 70 || lat < -60) return true;

                // Major land masses to EXCLUDE (continents)
                var landMasses = [
                    // North America
                    {latMin: 15, latMax: 72, lngMin: -170, lngMax: -52},
                    // South America
                    {latMin: -56, latMax: 13, lngMin: -82, lngMax: -34},
                    // Europe
                    {latMin: 36, latMax: 71, lngMin: -10, lngMax: 40},
                    // Africa
                    {latMin: -35, latMax: 37, lngMin: -18, lngMax: 52},
                    // Asia
                    {latMin: 0, latMax: 55, lngMin: 60, lngMax: 150},
                    // Australia
                    {latMin: -44, latMax: -10, lngMin: 113, lngMax: 154},
                    // Greenland
                    {latMin: 60, latMax: 83, lngMin: -73, lngMax: -12}
                ];

                // Check if point is in any land mass
                for (var i = 0; i < landMasses.length; i++) {
                    var land = landMasses[i];
                    if (lat >= land.latMin && lat <= land.latMax && 
                        lng >= land.lngMin && lng <= land.lngMax) {
                        return false; // It's on land
                    }
                }

                // If not on land, assume it's water
                return true;
            }

            var tsunamiData = null;
            if (is_water(waypointLocation.lat, waypointLocation.lng)) {
                var rho = 1000;
                var g = 9.81;
                var k = 0.18;
                var initial_wave_height = k * Math.pow(kineticEnergy / (rho * g), 0.25);
                var tsunami_radius_km = 500 * (diameter_m / 1000);

                var tsunamiCircle = L.circle(waypointLocation, {
                    radius: tsunami_radius_km * 1000,
                    color: '#3498db',
                    fillColor: '#3498db',
                    fillOpacity: 0.15,
                    weight: 2,
                    dashArray: '10, 10',
                    interactive: false
                }).addTo(map);

                var tsunamiLabel = L.marker([waypointLocation.lat, waypointLocation.lng], {
                    icon: L.divIcon({
                        className: 'impact-label',
                        html: '<div style="background:rgba(52,152,219,0.9);color:white;padding:5px;border-radius:3px;font-size:11px;font-weight:bold;white-space:nowrap;">Tsunami: ' + tsunami_radius_km.toFixed(1) + ' km</div>',
                        iconSize: [120, 20]
                    }),
                    interactive: false
                }).addTo(map);

                impactLayers.tsunami = {layer: tsunamiCircle, center: waypointLocation, radius: tsunami_radius_km * 1000, label: tsunamiLabel};
                tsunamiData = {waveHeight: initial_wave_height, radius: tsunami_radius_km};
            }

            if (waypointMarker) {
                map.removeLayer(waypointMarker);
                waypointMarker = null;
            }

            showImpactResults(selectedAsteroid, waypointLocation, {
                crater: {diameter: craterDiameter, energy: kineticEnergy},
                shockwave: {radius: shockwaveRadius / 1000},
                wind: {radius: R_target / 1000, speed: target_wind_kmh},
                seismic: {
                    magnitude: magnitude,
                    strong: strongShakingRadius,
                    moderate: moderateShakingRadius,
                    light: lightShakingRadius
                },
                tsunami: tsunamiData
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

    // Initial event listener setup
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

    function showImpactResults(asteroid, location, data) {
        var sidebar = document.getElementById('sidebar-content');
        var html = '<button class="back-button" onclick="resetToAsteroidList()">← Back to Asteroid List</button>';
        html += '<h2>Impact Analysis</h2>';
        html += '<div style="background-color:#34495e; padding:15px; border-radius:5px; margin-bottom:20px;">';
        html += '<strong>' + asteroid.name + '</strong><br>';
        html += 'Diameter: ' + asteroid.diameter.toFixed(2) + ' m<br>';
        html += 'Mass: ' + formatMassMT(asteroid.mass_kg) + '<br>';
        html += 'Velocity: ' + asteroid.velocity_kmh.toLocaleString(undefined, {maximumFractionDigits: 0}) + ' km/h<br>';
        html += 'Impact Energy: ' + formatEnergyTNT(data.crater.energy);
        html += '</div>';

        html += '<div class="impact-zone" data-zone="crater">';
        html += '<h3>🎯 IMPACT CRATER</h3>';
        html += '<p><strong>Crater Diameter:</strong> ' + data.crater.diameter.toFixed(2) + ' m</p>';
        html += '<p><strong>Asteroid Diameter:</strong> ' + asteroid.diameter.toFixed(2) + ' m</p>';
        html += '<p>Complete vaporization at ground zero. Crater forms from intense heat and pressure.</p>';
        html += '</div>';

        html += '<div class="impact-zone" data-zone="shockwave">';
        html += '<h3>💥 SHOCKWAVE ZONE</h3>';
        html += '<p><strong>Radius:</strong> ' + data.shockwave.radius.toFixed(2) + ' km</p>';
        html += '<p>Extreme destruction and widespread fires. All structures obliterated by supersonic blast wave.</p>';
        html += '</div>';

        html += '<div class="impact-zone" data-zone="wind">';
        html += '<h3>🌪️ WIND ZONE (≥60 km/h)</h3>';
        html += '<p><strong>Radius:</strong> ' + data.wind.radius.toFixed(2) + ' km</p>';
        html += '<p><strong>Wind Speed:</strong> ≥ ' + data.wind.speed + ' km/h</p>';
        html += '<p>Outer boundary of destructive winds. Trees uprooted, windows shattered, light structures damaged.</p>';
        html += '</div>';

        html += '<div class="impact-zone" data-zone="strongSeismic">';
        html += '<h3>🔴 STRONG SEISMIC ACTIVITY</h3>';
        html += '<p><strong>Magnitude:</strong> ' + data.seismic.magnitude.toFixed(2) + '</p>';
        html += '<p><strong>Radius:</strong> ' + data.seismic.strong.toFixed(2) + ' km</p>';
        html += '<p><strong>Intensity:</strong> MMI VII+</p>';
        html += '<p>Significant structural damage. Buildings collapse, ground cracks form, infrastructure fails.</p>';
        html += '</div>';

        html += '<div class="impact-zone" data-zone="moderateSeismic">';
        html += '<h3>🟠 MODERATE SEISMIC ACTIVITY</h3>';
        html += '<p><strong>Magnitude:</strong> ' + data.seismic.magnitude.toFixed(2) + '</p>';
        html += '<p><strong>Radius:</strong> ' + data.seismic.moderate.toFixed(2) + ' km</p>';
        html += '<p><strong>Intensity:</strong> MMI V-VI</p>';
        html += '<p>Felt by everyone. Furniture shifts, weak structures damaged, chimneys collapse.</p>';
        html += '</div>';

        html += '<div class="impact-zone" data-zone="lightSeismic">';
        html += '<h3>🟡 LIGHT SEISMIC ACTIVITY</h3>';
        html += '<p><strong>Magnitude:</strong> ' + data.seismic.magnitude.toFixed(2) + '</p>';
        html += '<p><strong>Radius:</strong> ' + data.seismic.light.toFixed(2) + ' km</p>';
        html += '<p><strong>Intensity:</strong> MMI III-IV</p>';
        html += '<p>Felt indoors by most. Hanging objects swing, slight vibrations, minor disturbances.</p>';
        html += '</div>';

        if (data.tsunami) {
            html += '<div class="impact-zone" data-zone="tsunami">';
            html += '<h3>🌊 TSUNAMI ZONE</h3>';
            html += '<p><strong>Initial Wave Height:</strong> ' + data.tsunami.waveHeight.toFixed(2) + ' m</p>';
            html += '<p><strong>Affected Radius:</strong> ' + data.tsunami.radius.toFixed(2) + ' km</p>';
            html += '<p>Ocean impact generates massive tsunami. Coastal areas at extreme risk from wave surge.</p>';
            html += '</div>';
        }

        sidebar.innerHTML = html;

        // Scroll event handler to highlight zones
        sidebar.addEventListener('scroll', handleSidebarScroll);

        // Initial focus on crater
        setTimeout(function() { focusZone('crater'); }, 100);
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

        // Fit map to show the zone
        map.fitBounds(bounds, {padding: [50, 50], maxZoom: 15});

        // Update sidebar highlighting
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
        // Remove all impact layers
        for (var key in impactLayers) {
            if (impactLayers[key].layer) {
                map.removeLayer(impactLayers[key].layer);
            }
             if (impactLayers[key].label) {
                map.removeLayer(impactLayers[key].label);
            }
        }
        impactLayers = {};

        // Reset map view
        map.setView([20, 0], 2);

        // Restore sidebar
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

        // Reset selections
        selectedAsteroid = null;
        waypointLocation = null;
        currentActiveZone = null;

        // Reattach event listeners after HTML replacement
        reattachAsteroidListEvents();
    }
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

import os
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
                            volume_m3 = (4/3) * math.pi * (radius_m**3)
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
                                "is_hazardous": True
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
        selectedAsteroid={name:name, diameter:diameter, mass_kg:mass};
        updateImpactButton();
    });

    function addAsteroid(ast){
        allAsteroids.push(ast);
        var massText=(ast.mass_kg!==undefined && ast.mass_kg!==null)?Number(ast.mass_kg).toExponential(2)+' kg':'N/A';
        var html='<div class="asteroid-item" data-name="'+ast.name+'" data-diameter="'+ast.diameter+'" data-mass="'+ast.mass_kg+'">'+
                 '<div>'+ast.name+'</div>'+
                 '<div>Diameter: '+ast.diameter.toFixed(2)+' m</div>'+
                 '<div>Mass: '+massText+'</div>'+
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

    var craterDiameter = selectedAsteroid.diameter * 20;

    // Get velocity if available (in km/h), otherwise assume 25000 km/h typical for NEAs
    var velocity_kmh = selectedAsteroid.velocity_kmh || 25000;
    var velocity_m_s = velocity_kmh * 1000 / 3600; // convert to m/s

    // Shockwave radius formula: proportional to cube root of kinetic energy
    // KE = 0.5 * mass * velocity^2
    var mass = selectedAsteroid.mass_kg || (Math.pow(selectedAsteroid.diameter / 2, 3) * 2000 * 4/3 * Math.PI); // fallback
    var kineticEnergy = 0.5 * mass * velocity_m_s * velocity_m_s; // Joules

    // Scale factor to convert energy to radius (meters) - tuned for visualization
    var shockwaveRadius = Math.pow(kineticEnergy, 1/3) * 0.05;

    var shockwave = L.circle(waypointLocation, {
        radius: shockwaveRadius,
        color: '#f1c40f', // yellow
        fillColor: '#f1c40f',
        fillOpacity: 0.2,
        weight: 2
    }).addTo(map);

    shockwave.bindPopup('SHOCKWAVE ZONE<br>' + 
        'Radius: ' + (shockwaveRadius / 1000).toFixed(2) + ' km<br>' +
        'Extreme destruction and fires<br>' +
        'Asteroid Mass: ' + (mass ? Number(mass).toExponential(2) + ' kg' : 'N/A') + 
        '<br>Velocity: ' + velocity_kmh.toFixed(0) + ' km/h'
    );

    var crater = L.circle(waypointLocation, {
        radius: craterDiameter,
        color: 'black',
        fillColor: '#000000',
        fillOpacity: 1,
        weight: 3
    }).addTo(map);

    crater.bindPopup(
        'IMPACT CRATER<br>' +
        'Asteroid: ' + selectedAsteroid.name + '<br>' +
        'Asteroid Diameter: ' + selectedAsteroid.diameter.toFixed(2) + ' meters<br>' +
        'Crater Diameter: ' + craterDiameter.toFixed(2) + ' meters<br>' +
        'Shockwave Radius: ' + (shockwaveRadius / 1000).toFixed(2) + ' km<br>' +
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
            if(result.done){document.getElementById('status-text').innerHTML='Stream complete'; return;}
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
        return jsonify({"status":"success", "api_key_present": bool(NASA_API_KEY), "response_code": r.status_code, "data_sample": r.json() if r.status_code==200 else r.text})
    except Exception as e:
        return jsonify({"status":"error","error":str(e),"api_key_present":bool(NASA_API_KEY)})


# --- Run app ---
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)

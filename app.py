import os
from flask import Flask, jsonify, Response
import folium
import requests
from dotenv import load_dotenv
import datetime
import json
import time

load_dotenv()

app = Flask(__name__)

NASA_API_KEY = os.getenv("NEO_API_KEY")

# Add CORS headers for streaming
@app.after_request
def after_request(response):
    response.headers.add('Access-Control-Allow-Origin', '*')
    response.headers.add('Access-Control-Allow-Headers', 'Content-Type')
    response.headers.add('Cache-Control', 'no-cache, no-store, must-revalidate')
    return response


def generate_asteroids():
    """Generator that yields hazardous asteroids one at a time."""
    count = 0
    start_time = time.time()
    timeout = 60  # 1 minute timeout

    print(f"=== STARTING ASTEROID SEARCH ===")
    print(f"API Key present: {NASA_API_KEY is not None and len(NASA_API_KEY) > 0}")

    # Define date range - START FROM RECENT and go backwards (more likely to find data quickly)
    # Search from 7 days ago back to 2015 (5 years of data should be plenty)
    end_date = datetime.date.today() - datetime.timedelta(days=7)
    start_date = datetime.date(2015, 1, 1)

    # Start from the END and work backwards for faster results
    current_date = end_date

    print("Starting asteroid search with 1-minute timeout...")

    while current_date >= start_date and count < 20:
        # Check timeout
        elapsed = time.time() - start_time
        if elapsed > timeout:
            print(f"Timeout reached after {count} asteroids ({elapsed:.1f}s)")
            break

        # Get 7 days at a time, going backwards
        batch_start = max(current_date - datetime.timedelta(days=6), start_date)

        date_str_start = batch_start.strftime("%Y-%m-%d")
        date_str_end = current_date.strftime("%Y-%m-%d")

        print(f"Fetching: {date_str_start} to {date_str_end} (Found: {count}/20, Time: {elapsed:.1f}s)")

        url = f"https://api.nasa.gov/neo/rest/v1/feed?start_date={date_str_start}&end_date={date_str_end}&api_key={NASA_API_KEY}"

        try:
            r = requests.get(url, timeout=10)
            print(f"API Response Status: {r.status_code}")
            
            if r.status_code != 200:
                print(f"API Error: {r.text}")
                current_date = batch_end + datetime.timedelta(days=1)
                continue
                
            data = r.json()

            # Loop through each date in the response
            for date_key in data.get("near_earth_objects", {}):
                if count >= 20 or time.time() - start_time > timeout:
                    break

                asteroids = data["near_earth_objects"][date_key]
                print(f"  Date {date_key}: {len(asteroids)} asteroids")

                for ast in asteroids:
                    if count >= 20 or time.time() - start_time > timeout:
                        break

                    # Only collect hazardous asteroids
                    if ast.get("is_potentially_hazardous_asteroid") == True:
                        # Get the closest approach distance
                        if ast.get("close_approach_data"):
                            miss_distance_km = float(ast["close_approach_data"][0]["miss_distance"]["kilometers"])

                            # Consider "really close" as less than 20 million km (lunar distance is ~384k km)
                            # This is still very close in astronomical terms!
                            if miss_distance_km < 20000000:
                                asteroid_data = {
                                    "name": ast["name"],
                                    "diameter": ast["estimated_diameter"]["meters"]["estimated_diameter_max"],
                                    "id": ast["id"],
                                    "miss_distance_km": miss_distance_km,
                                    "date": date_key,
                                    "is_hazardous": ast["is_potentially_hazardous_asteroid"]
                                }
                                count += 1
                                print(f"  ✓ Found hazardous asteroid #{count}: {ast['name']}")
                                yield asteroid_data

        except requests.exceptions.Timeout:
            print(f"Request timeout for {date_str_start}")
            # Move to previous batch even on timeout
            current_date = batch_start - datetime.timedelta(days=1)
            continue
        except Exception as e:
            print(f"Error fetching data: {e}")
            import traceback
            traceback.print_exc()
            # Move to previous batch even on error
            current_date = batch_start - datetime.timedelta(days=1)
            continue

        # Stop if we have 20 asteroids or timeout
        if count >= 20 or time.time() - start_time > timeout:
            break

        # Move to previous batch (going backwards in time)
        current_date = batch_start - datetime.timedelta(days=1)

    print(f"=== SEARCH COMPLETE: Found {count} asteroids in {time.time() - start_time:.1f}s ===")


def index():
    # Create map
    m = folium.Map(location=[20, 0], zoom_start=2)

    # Custom HTML/CSS/JS for sidebar and impact functionality
    custom_html = """
    <style>
    .sidebar {
        position: fixed;
        left: 0;
        top: 0;
        width: 350px;
        height: 100%;
        background-color: #2c3e50;
        color: white;
        padding: 20px;
        overflow-y: auto;
        z-index: 1000;
        box-shadow: 2px 0 5px rgba(0,0,0,0.3);
    }

    .sidebar h2 {
        margin-top: 0;
        font-size: 20px;
        border-bottom: 2px solid #e74c3c;
        padding-bottom: 10px;
    }

    .asteroid-item {
        background-color: #34495e;
        margin: 10px 0;
        padding: 15px;
        border-radius: 5px;
        cursor: pointer;
        transition: all 0.3s;
    }

    .asteroid-item:hover {
        background-color: #e74c3c;
        transform: translateX(5px);
    }

    .asteroid-item.selected {
        background-color: #e74c3c;
        border: 2px solid white;
    }

    .asteroid-name {
        font-weight: bold;
        font-size: 13px;
        margin-bottom: 5px;
    }

    .asteroid-info {
        font-size: 11px;
        color: #ecf0f1;
        margin: 3px 0;
    }

    .hazard-badge {
        display: inline-block;
        background-color: #e74c3c;
        color: white;
        padding: 2px 6px;
        border-radius: 3px;
        font-size: 10px;
        margin-top: 5px;
    }

    .impact-button {
        width: 100%;
        padding: 15px;
        background-color: #27ae60;
        color: white;
        border: none;
        border-radius: 5px;
        font-size: 16px;
        font-weight: bold;
        cursor: pointer;
        margin-top: 20px;
        transition: all 0.3s;
    }

    .impact-button:hover {
        background-color: #229954;
    }

    .impact-button:disabled {
        background-color: #95a5a6;
        cursor: not-allowed;
    }

    .info-text {
        font-size: 12px;
        color: #bdc3c7;
        margin-top: 10px;
        font-style: italic;
    }

    .loading-text {
        font-size: 14px;
        color: #ecf0f1;
        text-align: center;
        padding: 20px;
    }

    .status-text {
        font-size: 12px;
        color: #3498db;
        text-align: center;
        padding: 10px;
        font-style: italic;
    }

    .error-text {
        font-size: 12px;
        color: #e74c3c;
        text-align: center;
        padding: 10px;
    }

    #map {
        margin-left: 350px;
    }
    </style>

    <div class="sidebar">
        <h2>Hazardous Meteor Impacts</h2>
        <div class="status-text" id="status-text">Searching for asteroids...</div>
        <div id="asteroid-list"></div>
        <button class="impact-button" id="impact-btn" disabled>SIMULATE IMPACT</button>
        <div class="info-text">1. Select an asteroid<br>2. Click on map to place waypoint<br>3. Click "SIMULATE IMPACT"</div>
    </div>

    <script>
    var selectedAsteroid = null;
    var waypointMarker = null;
    var waypointLocation = null;
    var map = null;
    var allAsteroids = [];

    console.log('Starting asteroid fetch...');

    // Set up event delegation on the asteroid list container
    document.getElementById('asteroid-list').addEventListener('click', function(e) {
        // Find the clicked asteroid item
        var asteroidItem = e.target.closest('.asteroid-item');
        if (!asteroidItem) return;
        
        console.log('Clicked asteroid item');
        
        // Remove previous selection
        document.querySelectorAll('.asteroid-item').forEach(function(i) {
            i.classList.remove('selected');
        });

        // Select this one
        asteroidItem.classList.add('selected');
        
        var name = asteroidItem.getAttribute('data-name');
        var diameter = parseFloat(asteroidItem.getAttribute('data-diameter'));
        
        selectedAsteroid = {
            name: name,
            diameter: diameter
        };
        
        console.log('Selected asteroid:', selectedAsteroid);

        // Enable impact button if waypoint exists
        updateImpactButton();
    });

    // Stream asteroids as they arrive
    fetch('/stream_asteroids')
        .then(response => {
            console.log('Got response:', response.status);
            if (!response.ok) {
                throw new Error('Network response was not ok: ' + response.status);
            }
            const reader = response.body.getReader();
            const decoder = new TextDecoder();
            let buffer = '';

            function processText({ done, value }) {
                if (done) {
                    console.log('Stream complete');
                    if (allAsteroids.length === 0) {
                        document.getElementById('status-text').innerHTML = 
                            '<span class="error-text">No asteroids found. Check console and server logs.</span>';
                    } else {
                        document.getElementById('status-text').innerHTML = 
                            'Search complete! Found ' + allAsteroids.length + ' asteroids';
                    }
                    return;
                }

                buffer += decoder.decode(value, { stream: true });
                const lines = buffer.split('\n');
                buffer = lines.pop(); // Keep incomplete line in buffer

                lines.forEach(line => {
                    if (line.trim().startsWith('data: ')) {
                        try {
                            const jsonStr = line.substring(6); // Remove 'data: ' prefix
                            console.log('Received:', jsonStr);
                            const data = JSON.parse(jsonStr);
                            
                            if (data.status) {
                                document.getElementById('status-text').innerHTML = data.status;
                            } else if (data.asteroid) {
                                console.log('Adding asteroid:', data.asteroid.name);
                                addAsteroid(data.asteroid);
                            } else if (data.complete) {
                                document.getElementById('status-text').innerHTML = 
                                    'Search complete! Found ' + allAsteroids.length + ' asteroids';
                            } else if (data.error) {
                                document.getElementById('status-text').innerHTML = 
                                    '<span class="error-text">Error: ' + data.error + '</span>';
                            }
                        } catch (e) {
                            console.error('Error parsing JSON:', e, 'Line:', line);
                        }
                    }
                });

                return reader.read().then(processText);
            }

            return reader.read().then(processText);
        })
        .catch(error => {
            console.error('Fetch error:', error);
            document.getElementById('status-text').innerHTML = 
                '<span class="error-text">Error loading asteroids: ' + error.message + '</span>';
        });

    function addAsteroid(ast) {
        allAsteroids.push(ast);
        
        var hazardBadge = '<span class="hazard-badge">HAZARDOUS</span>';
        var asteroidHtml = '<div class="asteroid-item" data-diameter="' + ast.diameter + '" data-name="' + ast.name + '">' +
            '<div class="asteroid-name">' + ast.name + '</div>' +
            '<div class="asteroid-info">Diameter: ' + ast.diameter.toFixed(2) + ' m</div>' +
            '<div class="asteroid-info">Miss Distance: ' + (ast.miss_distance_km / 1000).toFixed(0) + 'k km</div>' +
            '<div class="asteroid-info">Date: ' + ast.date + '</div>' +
            hazardBadge +
            '</div>';
        
        document.getElementById('asteroid-list').innerHTML += asteroidHtml;
        document.getElementById('status-text').innerHTML = 
            'Found ' + allAsteroids.length + ' asteroid' + (allAsteroids.length === 1 ? '' : 's') + '... searching...';
        
        // Add click handler to new item
        var items = document.querySelectorAll('.asteroid-item');
        var newItem = items[items.length - 1];
        newItem.addEventListener('click', function() {
            // Remove previous selection
            document.querySelectorAll('.asteroid-item').forEach(function(i) {
                i.classList.remove('selected');
            });

            // Select this one
            this.classList.add('selected');
            selectedAsteroid = {
                name: this.getAttribute('data-name'),
                diameter: parseFloat(this.getAttribute('data-diameter'))
            };

            // Enable impact button if waypoint exists
            updateImpactButton();
        });
    }

    function updateImpactButton() {
        var btn = document.getElementById('impact-btn');
        if (selectedAsteroid && waypointLocation) {
            btn.disabled = false;
        } else {
            btn.disabled = true;
        }
    }

    // Wait for map to load
    setTimeout(function() {
        for (var key in window) {
            if (window[key] instanceof L.Map) {
                map = window[key];

                // Add click handler for waypoint
                map.on('click', function(e) {
                    // Remove previous waypoint
                    if (waypointMarker) {
                        map.removeLayer(waypointMarker);
                    }

                    // Add new waypoint
                    waypointLocation = e.latlng;
                    waypointMarker = L.marker(e.latlng, {
                        icon: L.icon({
                            iconUrl: 'https://raw.githubusercontent.com/pointhi/leaflet-color-markers/master/img/marker-icon-red.png',
                            shadowUrl: 'https://cdnjs.cloudflare.com/ajax/libs/leaflet/0.7.7/images/marker-shadow.png',
                            iconSize: [25, 41],
                            iconAnchor: [12, 41],
                            popupAnchor: [1, -34],
                            shadowSize: [41, 41]
                        })
                    }).addTo(map);
                    waypointMarker.bindPopup('Impact Target<br>Lat: ' + e.latlng.lat.toFixed(4) + '<br>Lng: ' + e.latlng.lng.toFixed(4)).openPopup();

                    updateImpactButton();
                });

                break;
            }
        }
    }, 1000);

    // Impact button handler
    document.getElementById('impact-btn').addEventListener('click', function() {
        if (!selectedAsteroid || !waypointLocation) return;

        // Calculate crater diameter: 20x the asteroid diameter
        var craterDiameter = selectedAsteroid.diameter * 20;

        // Create black crater circle
        var crater = L.circle(waypointLocation, {
            radius: craterDiameter,
            color: 'black',
            fillColor: '#000000',
            fillOpacity: 1,
            weight: 3
        });
        crater.addTo(map);
        crater.bindPopup('IMPACT CRATER<br>' + 
            'Asteroid: ' + selectedAsteroid.name + '<br>' +
            'Asteroid Diameter: ' + selectedAsteroid.diameter.toFixed(2) + ' meters<br>' +
            'Crater Diameter: ' + craterDiameter.toFixed(2) + ' meters<br>' +
            'Lat: ' + waypointLocation.lat.toFixed(4) + '<br>' +
            'Lng: ' + waypointLocation.lng.toFixed(4)).openPopup();

        // Remove waypoint marker
        if (waypointMarker) {
            map.removeLayer(waypointMarker);
            waypointMarker = null;
            waypointLocation = null;
        }

        // Keep asteroid selected but disable button until new waypoint is placed
        // Don't clear selectedAsteroid or remove selection styling
        document.getElementById('impact-btn').disabled = true;
    });
    </script>
    """

    m.get_root().html.add_child(folium.Element(custom_html))

    return m._repr_html_()


def stream_asteroids():
    """Stream asteroids as they are found using Server-Sent Events."""
    def generate():
        try:
            yield 'data: {"status": "Searching for hazardous asteroids..."}\n\n'
            
            found_any = False
            for asteroid in generate_asteroids():
                found_any = True
                # Send asteroid data
                yield f'data: {json.dumps({"asteroid": asteroid})}\n\n'
            
            if not found_any:
                yield 'data: {"error": "No asteroids found in search"}\n\n'
            
            # Send completion message
            yield 'data: {"complete": true}\n\n'
        except Exception as e:
            print(f"Error in stream_asteroids: {e}")
            import traceback
            traceback.print_exc()
            yield f'data: {json.dumps({"error": str(e)})}\n\n'
    
    return Response(generate(), mimetype='text/event-stream')


# Debug endpoint
@app.route('/test_api')
def test_api():
    """Test endpoint to verify API is working."""
    try:
        # Test a single request
        test_date = "2024-09-01"
        url = f"https://api.nasa.gov/neo/rest/v1/feed?start_date={test_date}&end_date={test_date}&api_key={NASA_API_KEY}"
        
        r = requests.get(url, timeout=10)
        
        return jsonify({
            "status": "success",
            "api_key_present": NASA_API_KEY is not None and len(NASA_API_KEY) > 0,
            "response_code": r.status_code,
            "data_sample": r.json() if r.status_code == 200 else r.text
        })
    except Exception as e:
        return jsonify({
            "status": "error",
            "error": str(e),
            "api_key_present": NASA_API_KEY is not None and len(NASA_API_KEY) > 0
        })


# Add routes
app.add_url_rule('/', 'index', index)
app.add_url_rule('/stream_asteroids', 'stream_asteroids', stream_asteroids)

if __name__ == "__main__":
    app.run(debug=True)

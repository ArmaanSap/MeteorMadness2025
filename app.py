import os
from flask import Flask, jsonify
import folium
import requests
import random
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)

NASA_API_KEY = os.getenv("NEO_API_KEY")


def get_hazardous_asteroids():
    """Fetch hazardous asteroids from a static date."""
    # Use a static date that has many asteroids
    date_str = "2020-06-18"

    url = f"https://api.nasa.gov/neo/rest/v1/feed?start_date={date_str}&end_date={date_str}&api_key={NASA_API_KEY}"
    r = requests.get(url)
    data = r.json()

    all_asteroids = data["near_earth_objects"][date_str]
    hazardous = [ast for ast in all_asteroids if ast["is_potentially_hazardous_asteroid"] == True]

    hazardous_list = []
    for ast in hazardous[:10]:  # Get up to 10 hazardous asteroids
        hazardous_list.append({
            "name": ast["name"],
            "diameter": ast["estimated_diameter"]["meters"]["estimated_diameter_max"],
            "id": ast["id"]
        })

    return hazardous_list


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
        width: 300px;
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
        font-size: 14px;
        margin-bottom: 5px;
    }

    .asteroid-diameter {
        font-size: 12px;
        color: #ecf0f1;
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

    #map {
        margin-left: 300px;
    }
    </style>

    <div class="sidebar">
        <h2>Asteroids</h2>
        <div id="asteroid-list">Loading asteroids...</div>
        <button class="impact-button" id="impact-btn" disabled>SIMULATE IMPACT</button>
        <div class="info-text">1. Select an asteroid<br>2. Click on map to place waypoint<br>3. Click "SIMULATE IMPACT"</div>
    </div>

    <script>
    var selectedAsteroid = null;
    var waypointMarker = null;
    var waypointLocation = null;
    var map = null;

    fetch('/get_asteroids')
        .then(response => response.json())
        .then(data => {
            var listHtml = '';
            data.asteroids.forEach(function(ast) {
                listHtml += '<div class="asteroid-item" data-diameter="' + ast.diameter + '" data-name="' + ast.name + '">' +
                    '<div class="asteroid-name">' + ast.name + '</div>' +
                    '<div class="asteroid-diameter">Diameter: ' + ast.diameter.toFixed(2) + ' m</div>' +
                    '</div>';
            });
            document.getElementById('asteroid-list').innerHTML = listHtml;

            // Add click handlers
            document.querySelectorAll('.asteroid-item').forEach(function(item) {
                item.addEventListener('click', function() {
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
            });
        });

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

        // Create black crater circle
        var crater = L.circle(waypointLocation, {
            radius: selectedAsteroid.diameter,
            color: 'black',
            fillColor: '#000000',
            fillOpacity: 1,
            weight: 3
        });
        crater.addTo(map);
        crater.bindPopup('💥 IMPACT CRATER<br>' + 
            'Asteroid: ' + selectedAsteroid.name + '<br>' +
            'Crater Diameter: ' + selectedAsteroid.diameter.toFixed(2) + ' meters<br>' +
            'Lat: ' + waypointLocation.lat.toFixed(4) + '<br>' +
            'Lng: ' + waypointLocation.lng.toFixed(4)).openPopup();

        
        if (waypointMarker) {
            map.removeLayer(waypointMarker);
            waypointMarker = null;
            waypointLocation = null;
        }

        // Reset button
        document.getElementById('impact-btn').disabled = true;
    });
    </script>
    """

    m.get_root().html.add_child(folium.Element(custom_html))

    return m._repr_html_()


def get_asteroids():
    """API endpoint to return list of hazardous asteroids."""
    asteroids = get_hazardous_asteroids()
    return jsonify({"asteroids": asteroids})


# Add routes
app.add_url_rule('/', 'index', index)
app.add_url_rule('/get_asteroids', 'get_asteroids', get_asteroids)

if __name__ == "__main__":
    app.run(debug=True)

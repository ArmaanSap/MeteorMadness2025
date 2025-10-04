from flask import Flask, render_template, request
import folium
import requests
import math

app = Flask(__name__)

NASA_API_KEY = "DEMO_KEY" 

def get_asteroid():
    """Fetch one random asteroid from NASA NEO API."""
    url = f"https://api.nasa.gov/neo/rest/v1/neo/3542519?api_key={NASA_API_KEY}"
    r = requests.get(url)
    data = r.json()
    diameter = data["estimated_diameter"]["meters"]["estimated_diameter_max"]
    velocity = float(data["close_approach_data"][0]["relative_velocity"]["kilometers_per_second"])
    return diameter, velocity

def simulate(lat, lng):
    diameter, velocity = get_asteroid()

    # mass = volume * density (kg)
    density = 3000  # kg/m^3 (rocky asteroid)
    radius = diameter / 2
    volume = (4/3) * math.pi * radius**3
    mass = volume * density

    v = velocity * 1000  # convert km/s -> m/s
    energy = 0.5 * mass * v**2
    megatons = energy / 4.184e15

    crater = (megatons ** (1/3)) * 1.2
    shock = crater * 4
    thermal = crater * 6

    return {
        "lat": lat,
        "lng": lng,
        "diameter": round(diameter,2),
        "velocity": round(velocity,2),
        "energy": round(megatons,2),
        "zones": {
            "crater": crater,
            "shock": shock,
            "thermal": thermal
        }
    }

@app.route("/")
def index():
    # default map
    m = folium.Map(location=[20,0], zoom_start=2)
    return m._repr_html_()

@app.route("/impact", methods=["POST"])
def impact():
    lat = float(request.form["lat"])
    lng = float(request.form["lng"])
    result = simulate(lat, lng)

    # create map centered on impact
    m = folium.Map(location=[lat, lng], zoom_start=4)

    # draw zones
    folium.Circle([lat, lng], radius=result["zones"]["thermal"]*1000,
                  color="blue", fill=True, fill_opacity=0.15).add_to(m)
    folium.Circle([lat, lng], radius=result["zones"]["shock"]*1000,
                  color="orange", fill=True, fill_opacity=0.25).add_to(m)
    folium.Circle([lat, lng], radius=result["zones"]["crater"]*1000,
                  color="red", fill=True, fill_opacity=0.35).add_to(m)

    folium.Marker([lat, lng],
                  popup=f"Asteroid Diameter: {result['diameter']} m<br>"
                        f"Speed: {result['velocity']} km/s<br>"
                        f"Energy: {result['energy']} Mt TNT").add_to(m)

    return m._repr_html_()

if __name__ == "__main__":
    app.run(debug=True)

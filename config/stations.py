"""
Weather station definitions for WeatherSafeClaw.

Each entry maps an ICAO station code to:
  - lat/lon (used for NWS API point lookup)
  - wfo: NWS Weather Forecast Office code
  - grid_x, grid_y: NWS gridpoint coordinates for that WFO

To find grid coordinates for a new station:
  GET https://api.weather.gov/points/{lat},{lon}
  → Look for "gridX", "gridY", "cwa" in the response.
"""

STATIONS: dict[str, dict] = {
    "KNYC": {
        "name": "New York City (Central Park)",
        "lat": 40.7789,
        "lon": -73.9692,
        "wfo": "OKX",
        "grid_x": 33,
        "grid_y": 37,
        "timezone": "America/New_York",
    },
    "KMIA": {
        "name": "Miami International Airport",
        "lat": 25.7959,
        "lon": -80.2870,
        "wfo": "MFL",
        "grid_x": 106,
        "grid_y": 51,
        "timezone": "America/New_York",
    },
    "KMDW": {
        "name": "Chicago Midway Airport",
        "lat": 41.7868,
        "lon": -87.7522,
        "wfo": "LOT",
        "grid_x": 70,
        "grid_y": 70,
        "timezone": "America/Chicago",
    },
    "KORD": {
        "name": "Chicago O'Hare Airport",
        "lat": 41.9742,
        "lon": -87.9073,
        "wfo": "LOT",
        "grid_x": 65,
        "grid_y": 73,
        "timezone": "America/Chicago",
    },
    "KBOS": {
        "name": "Boston Logan Airport",
        "lat": 42.3631,
        "lon": -71.0064,
        "wfo": "BOX",
        "grid_x": 64,
        "grid_y": 34,
        "timezone": "America/New_York",
    },
    "KLAX": {
        "name": "Los Angeles International Airport",
        "lat": 33.9425,
        "lon": -118.4081,
        "wfo": "LOX",
        "grid_x": 150,
        "grid_y": 48,
        "timezone": "America/Los_Angeles",
    },
    "KDFW": {
        "name": "Dallas/Fort Worth International Airport",
        "lat": 32.8998,
        "lon": -97.0403,
        "wfo": "FWD",
        "grid_x": 80,
        "grid_y": 61,
        "timezone": "America/Chicago",
    },
    "KSEA": {
        "name": "Seattle-Tacoma International Airport",
        "lat": 47.4480,
        "lon": -122.3088,
        "wfo": "SEW",
        "grid_x": 124,
        "grid_y": 69,
        "timezone": "America/Los_Angeles",
    },
    "KDEN": {
        "name": "Denver International Airport",
        "lat": 39.8561,
        "lon": -104.6737,
        "wfo": "BOU",
        "grid_x": 57,
        "grid_y": 61,
        "timezone": "America/Denver",
    },
    "KATL": {
        "name": "Atlanta Hartsfield-Jackson Airport",
        "lat": 33.6407,
        "lon": -84.4277,
        "wfo": "FFC",
        "grid_x": 53,
        "grid_y": 58,
        "timezone": "America/New_York",
    },
}


![simfrancisco_template_visualization](https://github.com/user-attachments/assets/8f470634-12d3-4ac0-a0c8-da7b64e6ffe7)

# SimFrancisco Template Sample

![React](https://img.shields.io/badge/React-18+-61dafb?style=flat&logo=react)
![Three.js](https://img.shields.io/badge/Three.js-r150+-000000?style=flat&logo=three.js)
![Python](https://img.shields.io/badge/Python-3.9+-3776ab?style=flat&logo=python)
![FastAPI](https://img.shields.io/badge/FastAPI-latest-009688?style=flat&logo=fastapi)

This repository contains the source code for **SimFrancisco**, a web-based, real-time traffic simulation for San Francisco. The project uses React and Three.js for the front-end visualization and a Python-based FastAPI server for the back-end simulation logic.

[Live Demo](https://lukehollis.github.io/simfrancisco_hack_template/)

## Project Layout

```
simfrancisco/
│
├─ client/         ─ Vite + React + Three.js front-end
│   └─ src/
│       ├─ main.jsx          ─ React renderer bootstrap
│       ├─ App.jsx           ─ React-Router shell
│       └─ views/            ─ React components for each simulation
│
└─ api/            ─ FastAPI micro-service (Python ≥3.9)
    ├─ main.py         ─ WebSocket-based simulation server
    └─ requirements.txt
```

## Quick Start

### 1. Configure Environment Variables

Before running the application, you need to set up your environment variables. Both the `client` and `api` directories contain a `.env.template` file that lists the required variables.

1.  **Copy the templates**:
    ```bash
    cp client/.env.template client/.env
    cp api/.env.template api/.env
    ```

2.  **Edit the `.env` files**:
    Open `client/.env` and `api/.env` and fill in the required values.

### 2. Front-end (Browser)

```bash
cd client
pnpm install        # Installs React, Three.js, etc.
pnpm run dev        # Opens http://localhost:5173
```

### 3. Back-end (Simulation Server)

The back-end is a FastAPI application that provides the simulation environment.

```bash
cd api
pip install -r requirements.txt
uvicorn main:app --reload
```


![simfrancisco_traffic_visualization](https://github.com/user-attachments/assets/9177044b-8888-4380-bae7-a6366cd3d0fa)

## Data Sources

The `api/data` directory contains GeoJSON files used for the simulation's map layer.

- `bart_lines.geojson`: Geographic data for Bay Area Rapid Transit (BART) lines.
- `muni_stops.geojson`: Locations of San Francisco Municipal Railway (Muni) stops.
- `sf_parcel_data.geojson`: Parcel data for San Francisco. This file is not included in the repository due to its size. You can download it from [this Google Drive folder](https://drive.google.com/drive/u/0/folders/1KzdQlpj4AHTmDZOhVkzYSKFqbJgalyG7).


## WebSocket Communication

The client and server communicate over a WebSocket connection. The server streams real-time data about agent states, emissions, and traffic light status to the client.

- **Endpoint**: `/ws/traffic`
- **Messages**:
  - `start`: Initializes the simulation with the specified map bounds and number of agents.
  - `update_bounds`: Updates the simulation area when the user pans or zooms the map.
  - `set_num_agents`: Adjusts the number of agents in the simulation.
  - `stop`: Halts the simulation.

## License

MIT © 2025

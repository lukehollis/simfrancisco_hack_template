from fastapi import FastAPI, WebSocket, HTTPException, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import json
import os
import asyncio
import logging
from services.traffic import DriveGraphEnv
from services.geojson import GeoJSONService

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="City Simulation API")

# Add CORS middleware to allow frontend to access API endpoints
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # In production, replace with specific origins
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/health")
def health():
    return {"status": "ok"}


@app.websocket("/ws/traffic")
async def websocket_endpoint_traffic(websocket: WebSocket):
    await websocket.accept()
    await websocket.send_json({"type": "info", "message": "WebSocket connection established."})
    env = None
    simulation_task = None
    agent_setter_task = None
    geojson_service = GeoJSONService()
    try:
        while True:
            data = await websocket.receive_json()
            event_type = data.get("type")

            if event_type == "start":
                bounds = data.get("bounds")
                if not bounds:
                    await websocket.send_json({"type": "error", "message": "Missing bounds"})
                    continue

                if simulation_task:
                    simulation_task.cancel()
                if agent_setter_task:
                    agent_setter_task.cancel()

                num_agents = data.get('num_agents', 1000)
                show_traffic_lights = data.get('show_traffic_lights', True)
                show_traffic_lanes = data.get('show_traffic_lanes', True)
                show_bart_lines = data.get('show_bart_lines', False)
                show_muni_stops = data.get('show_muni_stops', False)
                show_sf_parcels = data.get('show_sf_parcels', False)
                
                env = DriveGraphEnv(
                    bounds=bounds, 
                    num_agents=num_agents,
                    show_traffic_lights=show_traffic_lights,
                    show_traffic_lanes=show_traffic_lanes
                )
                await env.reset()

                # Also send initial road network data
                road_network_data = env.get_road_network_data()
                await websocket.send_json({
                    "type": "initial_road_network",
                    "lanes": road_network_data
                })

                # Send GeoJSON data based on toggles
                if show_bart_lines:
                    bart_data = geojson_service.get_bart_lines()
                    if bart_data:
                        await websocket.send_json({
                            "type": "bart_lines",
                            "data": bart_data
                        })

                if show_muni_stops:
                    muni_data = geojson_service.get_muni_stops()
                    if muni_data:
                        await websocket.send_json({
                            "type": "muni_stops",
                            "data": muni_data
                        })

                if show_sf_parcels:
                    parcels_data = geojson_service.get_sf_parcels_by_bbox(bounds)
                    await websocket.send_json({
                        "type": "sf_parcels",
                        "data": parcels_data
                    })

                simulation_task = asyncio.create_task(run_simulation(websocket, env))

            elif event_type == "update_bounds":
                bounds = data.get("bounds")
                if not bounds:
                    await websocket.send_json({"type": "error", "message": "Missing bounds"})
                    continue
                if env:
                    show_traffic_lights = data.get('show_traffic_lights', True)
                    show_traffic_lanes = data.get('show_traffic_lanes', True)
                    show_bart_lines = data.get('show_bart_lines', False)
                    show_muni_stops = data.get('show_muni_stops', False)
                    show_sf_parcels = data.get('show_sf_parcels', False)
                    
                    env.update_bounds(bounds, show_traffic_lights, show_traffic_lanes)
                    
                    # Send updated road network data
                    road_network_data = env.get_road_network_data()
                    await websocket.send_json({
                        "type": "road_network_update",
                        "lanes": road_network_data
                    })

                    # Send updated GeoJSON data based on toggles
                    if show_bart_lines:
                        bart_data = geojson_service.get_bart_lines()
                        if bart_data:
                            await websocket.send_json({
                                "type": "bart_lines",
                                "data": bart_data
                            })

                    if show_muni_stops:
                        muni_data = geojson_service.get_muni_stops()
                        if muni_data:
                            await websocket.send_json({
                                "type": "muni_stops",
                                "data": muni_data
                            })

                    if show_sf_parcels:
                        parcels_data = geojson_service.get_sf_parcels_by_bbox(bounds)
                        await websocket.send_json({
                            "type": "sf_parcels",
                            "data": parcels_data
                        })
                else:
                    await websocket.send_json({"type": "error", "message": "Simulation not started"})

            elif event_type == "set_num_agents":
                if env:
                    num_agents = data.get('num_agents')
                    if num_agents is not None:
                        if agent_setter_task and not agent_setter_task.done():
                            agent_setter_task.cancel()
                        
                        await websocket.send_json({"type": "info", "message": f"Setting agent count to {num_agents} in the background..."})
                        agent_setter_task = asyncio.create_task(env.set_num_agents(num_agents))
                    else:
                        await websocket.send_json({"type": "error", "message": "Missing num_agents"})
                else:
                    await websocket.send_json({"type": "error", "message": "Simulation not started"})

            elif event_type == "stop":
                if simulation_task:
                    simulation_task.cancel()
                    simulation_task = None
                if agent_setter_task:
                    agent_setter_task.cancel()
                    agent_setter_task = None
                await websocket.send_json({"type": "info", "message": "Simulation stopped"})

    except WebSocketDisconnect:
        logger.info("Client disconnected from websocket")
        if simulation_task:
            simulation_task.cancel()
        if agent_setter_task:
            agent_setter_task.cancel()
    except Exception as e:
        logger.error(f"Error in websocket: {e}", exc_info=True)
        # The connection might be closed already, so this might fail
        try:
            await websocket.send_json({"type": "error", "message": str(e)})
        except Exception as send_error:
            logger.error(f"Could not send error to client: {send_error}")


async def run_simulation(websocket: WebSocket, env: DriveGraphEnv):
    """Coroutine to run the simulation and send updates."""
    try:
        while True:
            await env.step()
            agent_states = env.get_agent_states()
            emissions_data = env.get_emissions_data()
            traffic_lights = env.get_traffic_light_states()
            await websocket.send_json({
                "type": "update",
                "agents": agent_states,
                "emissions": emissions_data,
                "traffic_lights": traffic_lights
            })
            await asyncio.sleep(0.1) # 10 updates per second
    except asyncio.CancelledError:
        logger.info("Simulation task was cancelled.")
    except Exception as e:
        logger.error(f"Error during simulation: {e}", exc_info=True)
        try:
            await websocket.send_json({"type": "error", "message": f"Simulation failed: {e}"})
        except Exception as send_error:
            logger.error(f"Could not send simulation error to client: {send_error}")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)

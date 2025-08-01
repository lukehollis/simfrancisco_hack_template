import React, { useState, useEffect, useRef, useMemo, useCallback } from 'react';
import { DeckGL } from '@deck.gl/react';
import { LineLayer, ScatterplotLayer, GeoJsonLayer } from '@deck.gl/layers';
import { HeatmapLayer } from '@deck.gl/aggregation-layers';
import { TripsLayer } from '@deck.gl/geo-layers';
import { Map as MapboxMap } from 'react-map-gl';
import { Text, Slider, Toggle } from '@geist-ui/core';
import ControlPanel from '../components/ControlPanel.jsx';
import InfoPanel from '../components/InfoPanel.jsx';
import DebugConsole from '../components/DebugConsole.jsx';
import config from '../config.js';
import 'mapbox-gl/dist/mapbox-gl.css';

const WS_URL = `${config.WS_BASE_URL}/ws/traffic`;
const MAPBOX_TOKEN = import.meta.env.VITE_MAPBOX_TOKEN;

const INITIAL_VIEW_STATE = {
    longitude: -122.399255,
    latitude: 37.792633,
    zoom: 14,
    pitch: 30,
    bearing: 0
};

export default function SFMap() {
    const [agents, setAgents] = useState([]);
    const [emissions, setEmissions] = useState([]);
    const [trafficLights, setTrafficLights] = useState([]);
    const [trafficLightPositions, setTrafficLightPositions] = useState(new Map()); // Cache positions
    const [roadNetwork, setRoadNetwork] = useState(null);
    const [numAgents, setNumAgents] = useState(1024);
    const [error, setError] = useState(null);
    const [showTrails, setShowTrails] = useState(true);
    const [showTrafficLights, setShowTrafficLights] = useState(true);
    const [showTrafficLanes, setShowTrafficLanes] = useState(false);
    const [showBartLines, setShowBartLines] = useState(false);
    const [showMuniStops, setShowMuniStops] = useState(false);
    const [showSfParcels, setShowSfParcels] = useState(false);
    const [animationSpeed, setAnimationSpeed] = useState(8);
    const [trailLength, setTrailLength] = useState(64);
    const [time, setTime] = useState(0);
    const [showEmissions, setShowEmissions] = useState(false);
    const [logs, setLogs] = useState([]);
    const [bartLines, setBartLines] = useState(null);
    const [muniStops, setMuniStops] = useState(null);
    const [sfParcels, setSfParcels] = useState(null);
    const wsRef = useRef(null);
    const viewStateRef = useRef(INITIAL_VIEW_STATE);

    const isInitialLoadRef = useRef(true);

    const connect = useCallback(() => {
        const ws = new WebSocket(WS_URL);
        wsRef.current = ws;

        ws.onopen = () => {
            console.log('WS opened');
            sendBounds(true); 
        };

        ws.onmessage = (ev) => {
            try {
                const parsed = JSON.parse(ev.data);

                setLogs(prev => {
                    const message = parsed.message || `Received data: ${ev.data.substring(0, 100)}...`;
                    const newLog = { type: parsed.type, message: message };
                    return [newLog, ...prev].slice(0, 50);
                });


                if (parsed.type === 'error') {
                    setError(parsed.message);
                    return;
                }

                if (parsed.type === 'update') {
                    const agentArray = Object.values(parsed.agents);
                    setAgents(agentArray);
                    if (parsed.emissions) {
                        setEmissions(parsed.emissions);
                    }
                    if (showTrafficLights && parsed.traffic_lights) {
                        // Cache positions and update traffic lights with stable positions
                        setTrafficLightPositions(prev => {
                            const newPositions = new Map(prev);
                            const updatedLights = [];
                            
                            parsed.traffic_lights.forEach(light => {
                                if (!newPositions.has(light.id)) {
                                    // First time seeing this traffic light, cache its position
                                    newPositions.set(light.id, light.position);
                                    updatedLights.push(light);
                                } else {
                                    // Use cached position for existing traffic lights
                                    updatedLights.push({
                                        ...light,
                                        position: newPositions.get(light.id)
                                    });
                                }
                            });
                            
                            setTrafficLights(updatedLights);
                            return newPositions;
                        });
                    } else {
                        setTrafficLights([]);
                    }
                } else if (parsed.type === 'initial_road_network' || parsed.type === 'road_network_update') {
                    if (showTrafficLanes && parsed.lanes) {
                        setRoadNetwork(parsed.lanes);
                    } else {
                        setRoadNetwork(null);
                    }
                } else if (parsed.type === 'bart_lines') {
                    if (showBartLines && parsed.data) {
                        setBartLines(parsed.data);
                    } else {
                        setBartLines(null);
                    }
                } else if (parsed.type === 'muni_stops') {
                    if (showMuniStops && parsed.data) {
                        setMuniStops(parsed.data);
                    } else {
                        setMuniStops(null);
                    }
                } else if (parsed.type === 'sf_parcels') {
                    if (showSfParcels && parsed.data) {
                        setSfParcels(parsed.data);
                    } else {
                        setSfParcels(null);
                    }
                }
            } catch (e) {
                console.error("Failed to process message: ", e);
                setLogs(prev => [{ type: 'error', message: `Failed to process message: ${e}` }, ...prev].slice(0, 50));
            }
        };

        ws.onclose = () => {
            console.log('WS closed');
            setLogs(prev => [{ type: 'info', message: 'WebSocket connection closed.' }, ...prev].slice(0, 50));
        };
        
        ws.onerror = (err) => {
            console.error("WebSocket error:", err);
            setError("WebSocket connection failed. Please check the server.");
            setLogs(prev => [{ type: 'error', message: "WebSocket connection failed." }, ...prev].slice(0, 50));
        }

    }, []);

    useEffect(() => {
        connect();
        return () => {
            if (wsRef.current) {
                wsRef.current.close();
            }
        };
    }, [connect]);

    // Clear traffic light position cache when traffic lights are disabled
    useEffect(() => {
        if (!showTrafficLights) {
            setTrafficLightPositions(new Map());
        }
    }, [showTrafficLights]);

    // Clear GeoJSON data when toggles are disabled
    useEffect(() => {
        if (!showBartLines) {
            setBartLines(null);
        }
    }, [showBartLines]);

    useEffect(() => {
        if (!showMuniStops) {
            setMuniStops(null);
        }
    }, [showMuniStops]);

    useEffect(() => {
        if (!showSfParcels) {
            setSfParcels(null);
        }
    }, [showSfParcels]);

    // Animation loop for trails
    useEffect(() => {
        if (!showTrails) return;
        
        const animate = () => {
            setTime(t => (t + (animationSpeed/1000)));
        };
        
        const animationId = requestAnimationFrame(animate);
        
        return () => {
            if (animationId) {
                cancelAnimationFrame(animationId);
            }
        };
        }, [showTrails, time, animationSpeed]);
    
    const sendBounds = (isInitial = false) => {
        if (wsRef.current && wsRef.current.readyState === WebSocket.OPEN) {
            const { longitude, latitude, zoom } = viewStateRef.current;
            
            const lngDiff = 360 / Math.pow(2, zoom);
            const latDiff = lngDiff;

            const bounds = {
                minLng: longitude - lngDiff,
                maxLng: longitude + lngDiff,
                minLat: latitude - latDiff,
                maxLat: latitude + latDiff
            };
            
            const messageType = isInitial ? 'start' : 'update_bounds';
            const payload = {
                type: messageType,
                bounds,
                num_agents: numAgents,
                show_traffic_lights: showTrafficLights,
                show_traffic_lanes: showTrafficLanes,
                show_bart_lines: showBartLines,
                show_muni_stops: showMuniStops,
                show_sf_parcels: showSfParcels
            };
            wsRef.current.send(JSON.stringify(payload));
        }
    }
    
    const debounce = (func, delay) => {
        let timeout;
        return (...args) => {
            clearTimeout(timeout);
            timeout = setTimeout(() => func(...args), delay);
        };
    };

    const debouncedSendBounds = useMemo(() => debounce(() => sendBounds(false), 1000), [showTrafficLights, showTrafficLanes, showBartLines, showMuniStops, showSfParcels]);

    const onViewStateChange = ({ viewState }) => {
        viewStateRef.current = viewState;
        debouncedSendBounds();
    };

    const sendAgentUpdate = (count) => {
        if (wsRef.current && wsRef.current.readyState === WebSocket.OPEN) {
            wsRef.current.send(JSON.stringify({ type: 'set_num_agents', num_agents: count }));
        }
    }

    const debouncedSendAgentUpdate = useMemo(() => debounce(sendAgentUpdate, 500), []);

    const handleNumAgentsChange = (val) => {
        setNumAgents(val);
        debouncedSendAgentUpdate(val);
    };

    // Use emissions data from backend (no client-side computation needed)

    const layers = useMemo(() => {
        const layerList = [];
        
        if (!agents.length) return layerList;

        // Emissions Heatmap Layer (render first so it appears behind other layers)
        if (showEmissions && emissions.length > 0) {
            const emissionsLayer = new HeatmapLayer({
                id: 'emissions-heatmap',
                data: emissions,
                getPosition: d => d.position,
                getWeight: d => d.weight,
                radiusPixels: 100, // Make it more diffuse
                colorRange: [
                    [0, 255, 0, 0],      // Transparent green
                    [50, 255, 50, 60],   // Lighter green
                    [100, 255, 100, 120], // Medium green
                    [150, 255, 150, 180], // Bright green
                    [200, 255, 0, 210],  // Yellow-green
                    [255, 255, 0, 240]   // Bright yellow
                ],
                intensity: 1,
                threshold: 0.01, // Lower threshold for more visible emissions
                pickable: false
            });
            layerList.push(emissionsLayer);
        }

        // Road Network Lanes
        if (showTrafficLanes && roadNetwork) {
            const roadLanesLayer = new GeoJsonLayer({
                id: 'road-network-lanes',
                data: roadNetwork,
                stroked: true,
                getLineColor: [255, 255, 255, 50],
                getLineWidth: 2,
                lineWidthMinPixels: 1,
            });
            layerList.push(roadLanesLayer);
        }

        // BART Lines
        if (showBartLines && bartLines) {
            const bartLinesLayer = new GeoJsonLayer({
                id: 'bart-lines',
                data: bartLines,
                stroked: true,
                filled: false,
                getLineColor: d => {
                    // Color code BART lines based on their name
                    const colors = {
                        'R': [255, 0, 0, 255],      // Red line
                        'O': [255, 165, 0, 255],    // Orange line  
                        'Y': [255, 255, 0, 255],    // Yellow line
                        'Y1': [255, 255, 0, 255],   // Yellow line
                        'Y2': [255, 255, 0, 255],   // Yellow line
                        'G': [0, 255, 0, 255],      // Green line
                        'B': [0, 0, 255, 255],      // Blue line
                        'W': [128, 0, 128, 255],    // Purple line (Warm Springs)
                        'L': [192, 192, 192, 255],  // Silver line (Legacy)
                        'M': [0, 0, 255, 255],      // Blue line (Mill/South Bay)
                        'H': [255, 192, 203, 255],  // Pink line (Heritage)
                        'E': [0, 255, 255, 255]     // Cyan line (eBART)
                    };
                    return colors[d.properties?.name] || [255, 255, 255, 255];
                },
                getLineWidth: 3,
                lineWidthMinPixels: 2,
                lineWidthMaxPixels: 8,
            });
            layerList.push(bartLinesLayer);
        }

        // Muni Stops
        if (showMuniStops && muniStops) {
            const muniStopsLayer = new GeoJsonLayer({
                id: 'muni-stops',
                data: muniStops,
                filled: true,
                stroked: true,
                pointType: 'circle',
                getFillColor: [255, 140, 0, 200],  // Orange color
                getLineColor: [255, 255, 255, 255],
                getPointRadius: 1,
                pointRadiusMinPixels: 1,
                pointRadiusMaxPixels: 3,
                getLineWidth: 1,
                pickable: true,
                onHover: info => {
                    if (info.object) {
                        // Could add tooltip functionality here
                        console.log('Muni Stop:', info.object.properties?.STOPNAME);
                    }
                }
            });
            layerList.push(muniStopsLayer);
        }

        // SF Parcels
        if (showSfParcels && sfParcels) {
            const sfParcelsLayer = new GeoJsonLayer({
                id: 'sf-parcels',
                data: sfParcels,
                filled: true,
                stroked: true,
                getFillColor: d => {
                    // Color code based on land use
                    const landuse = d.properties?.landuse;
                    switch (landuse) {
                        case 'RESIDENT': return [100, 150, 255, 100];  // Light blue
                        case 'MIXRES': return [150, 100, 255, 100];    // Light purple  
                        case 'RETAIL/ENT': return [255, 100, 100, 100]; // Light red
                        case 'OFFICE': return [100, 255, 100, 100];    // Light green
                        case 'MIXED': return [255, 255, 100, 100];     // Light yellow
                        case 'PDR': return [255, 150, 100, 100];       // Light orange
                        case 'VACANT': return [200, 200, 200, 100];    // Light gray
                        default: return [150, 150, 150, 100];          // Default gray
                    }
                },
                getLineColor: [255, 255, 255, 150],
                getLineWidth: 1,
                lineWidthMinPixels: 0.5,
                pickable: true,
                onHover: info => {
                    if (info.object) {
                        // Could add tooltip functionality here
                        const props = info.object.properties;
                        console.log('Parcel:', props?.street, props?.from_st, '- Land Use:', props?.landuse);
                    }
                }
            });
            layerList.push(sfParcelsLayer);
        }

        if (showTrafficLights && trafficLights.length > 0) {
            const trafficLightLayer = new ScatterplotLayer({
                id: 'traffic-lights',
                data: trafficLights,
                getPosition: d => d.position,
                getFillColor: d => d.state === 'red' ? [255, 0, 0, 255] : [0, 255, 0, 255],
                getRadius: 1,
                radiusMinPixels: 1,
                radiusMaxPixels: 3,
            });
            layerList.push(trafficLightLayer);
        }
 
        if (showTrails) {
            const trailData = agents
                .filter(a => a.path && a.path.length > 1)
                .map(a => {
                    const path = a.path.slice(-100); // Use last 100 points
                    const timestamps = path.map((_, i) => i);
                    return {
                        path: path.map(p => [p[1], p[0]]),
                        timestamps,
                    };
                });

            const trails = new TripsLayer({
                id: 'agent-trails',
                data: trailData,
                getPath: d => d.path,
                getTimestamps: d => d.timestamps,
                getColor: [255, 140, 0, 200],
                getWidth: 8,
                widthMinPixels: 2,
                widthMaxPixels: 10,
                trailLength: trailLength / 10,
                currentTime: time % 100,
                uniqueIdProperty: 'id'
            });

            layerList.push(trails);
        } else {
            const agentPoints = new ScatterplotLayer({
                id: 'agent-points',
                data: agents,
                getPosition: d => [d.position[1], d.position[0]],
                getFillColor: [255, 140, 0],
                getRadius: 20,
                radiusMinPixels: 2,
                radiusMaxPixels: 20,
            });

            const agentPaths = new LineLayer({
                id: 'agent-paths',
                data: agents.filter(a => a.path && a.path.length > 1),
                getPath: d => d.path.map(p => [p[1], p[0]]),
                getColor: [0, 255, 0, 100],
                getWidth: 3,
                widthMinPixels: 1,
            });

            layerList.push(agentPaths, agentPoints);
        }
        
        return layerList;
    }, [agents, showTrails, time, trailLength, showEmissions, emissions, trafficLights, roadNetwork, showTrafficLights, showTrafficLanes, showBartLines, showMuniStops, showSfParcels, bartLines, muniStops, sfParcels]);

    if (error) {
        return (
            <div style={{ width: '100vw', height: '100vh', display: 'flex', flexDirection: 'column', justifyContent: 'center', alignItems: 'center', background: 'rgba(0, 0, 0, 0.9)', color: '#ffaaaa' }}>
                <Text h2>Error Connecting to Simulation</Text>

                <Text p>{error}</Text>
            </div>
        );
    }

    return (
        <div style={{ width: '100vw', height: '100vh', overflow: 'hidden', background: '#000011' }}>
            <ControlPanel>
                <Text h1 style={{ fontFamily: 'monospace', textTransform: 'uppercase', fontSize: '18px' }}>[ SF Sim ]</Text>

                <div style={{ display: 'flex', flexDirection: 'column', gap: '15px', width: '100%', alignItems: 'stretch' }}>

                    <div>
                        <Text style={{ fontSize: '12px', fontFamily: 'monospace' }}># of Agents</Text>
                        <Slider 
                            value={numAgents}
                            min={100}
                            max={10000}
                            step={100}
                            onChange={handleNumAgentsChange} 
                        />
                    </div>
                    <div>
                        <Text style={{ fontSize: '12px', fontFamily: 'monospace' }}>Animation Speed</Text>
                        <Slider 
                            value={animationSpeed}
                            min={1}
                            max={50}
                            step={1}
                            onChange={setAnimationSpeed} 
                        />
                    </div>
                    <div>
                        <Text style={{ fontSize: '12px', fontFamily: 'monospace' }}>Trail Length</Text>
                        <Slider 
                            value={trailLength}
                            min={1}
                            max={300}
                            step={1}
                            onChange={setTrailLength} 
                        />
                    </div>
                    <div>
                        <Text style={{ fontSize: '12px', fontFamily: 'monospace' }}>Emissions Heatmap</Text>
                        <label style={{ display: 'flex', alignItems: 'center', cursor: 'pointer' }}>
                            <span style={{ fontSize: '12px', marginRight: '10px' }}>
                                {showEmissions ? 'On' : 'Off'}
                            </span>
                            <Toggle 
                                checked={showEmissions}
                                onChange={(e) => setShowEmissions(e.target.checked)}
                            />
                        </label>
                    </div>
                    <div>
                        <Text style={{ fontSize: '12px', fontFamily: 'monospace' }}>Traffic Lights</Text>
                        <label style={{ display: 'flex', alignItems: 'center', marginTop: '5px', cursor: 'pointer' }}>
                            <span style={{ fontSize: '14px', marginRight: '10px' }}>
                                {showTrafficLights ? 'On' : 'Off'}
                            </span>
                            <Toggle 
                                checked={showTrafficLights}
                                onChange={(e) => setShowTrafficLights(e.target.checked)}
                            />
                        </label>
                    </div>
                    <div>
                        <Text style={{ fontSize: '12px', fontFamily: 'monospace' }}>Traffic Lanes</Text>
                        <label style={{ display: 'flex', alignItems: 'center', marginTop: '5px', cursor: 'pointer' }}>
                            <span style={{ fontSize: '14px', marginRight: '10px' }}>
                                {showTrafficLanes ? 'On' : 'Off'}
                            </span>
                            <Toggle 
                                checked={showTrafficLanes}
                                onChange={(e) => setShowTrafficLanes(e.target.checked)}
                            />
                        </label>
                    </div>
                    <div>
                        <Text style={{ fontSize: '12px', fontFamily: 'monospace' }}>BART Lines</Text>
                        <label style={{ display: 'flex', alignItems: 'center', marginTop: '5px', cursor: 'pointer' }}>
                            <span style={{ fontSize: '14px', marginRight: '10px' }}>
                                {showBartLines ? 'On' : 'Off'}
                            </span>
                            <Toggle 
                                checked={showBartLines}
                                onChange={(e) => setShowBartLines(e.target.checked)}
                            />
                        </label>
                    </div>
                    <div>
                        <Text style={{ fontSize: '12px', fontFamily: 'monospace' }}>Muni Stops</Text>
                        <label style={{ display: 'flex', alignItems: 'center', marginTop: '5px', cursor: 'pointer' }}>
                            <span style={{ fontSize: '14px', marginRight: '10px' }}>
                                {showMuniStops ? 'On' : 'Off'}
                            </span>
                            <Toggle 
                                checked={showMuniStops}
                                onChange={(e) => setShowMuniStops(e.target.checked)}
                            />
                        </label>
                    </div>
                    <div>
                        <Text style={{ fontSize: '12px', fontFamily: 'monospace' }}>SF Parcels</Text>
                        <label style={{ display: 'flex', alignItems: 'center', marginTop: '5px', cursor: 'pointer' }}>
                            <span style={{ fontSize: '14px', marginRight: '10px' }}>
                                {showSfParcels ? 'On' : 'Off'}
                            </span>
                            <Toggle 
                                checked={showSfParcels}
                                onChange={(e) => setShowSfParcels(e.target.checked)}
                            />
                        </label>
                    </div>
                </div>
            </ControlPanel>
            <DeckGL
                initialViewState={INITIAL_VIEW_STATE}
                controller={true}
                layers={layers}
                onViewStateChange={onViewStateChange}
            >
                <MapboxMap
                    mapboxAccessToken={MAPBOX_TOKEN}
                    mapStyle="mapbox://styles/mapbox/dark-v11"
                />
            </DeckGL>
            <InfoPanel>
                <DebugConsole logs={logs} />
            </InfoPanel>
        </div>
    );
}


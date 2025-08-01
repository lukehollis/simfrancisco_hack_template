import numpy as np
import torch
from gymnasium.spaces import Box, Dict as SpaceDict, Discrete
from typing import Dict, List, Tuple, Any, Optional, Union, Set
import logging
from dataclasses import dataclass
import math
import osmnx as ox
import geopandas as gpd
from pyproj import CRS
from shapely.geometry import Point, Polygon, LineString
import networkx as nx
import json
from networkx.readwrite import json_graph
import sys
import os
import asyncio
import tempfile
import shutil
from osmnx._errors import InsufficientResponseError
import hashlib

logger = logging.getLogger(__name__)

# Custom JSON encoder to handle numpy types
class NpEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, np.integer):
            return int(obj)
        if isinstance(obj, np.floating):
            return float(obj)
        if isinstance(obj, np.ndarray):
            return obj.tolist()
        return super(NpEncoder, self).default(obj)

# Constants for the urban environment
MAX_AGENTS = 100000
MAX_SPEED = 0.0005

@dataclass
class AgentState:
    """Represents the state of a single agent in the urban environment"""
    agent_id: str
    position: np.ndarray
    velocity: np.ndarray
    goal: np.ndarray
    path: Optional[List[int]] = None
    path_index: int = 0
    path_positions: Optional[np.ndarray] = None
    
    def to_tensor(self) -> torch.Tensor:
        """Convert agent state to tensor representation"""
        state = np.concatenate([self.position, self.velocity, self.goal])
        return torch.tensor(state, dtype=torch.float32)

class DriveGraphEnv:
    """
    DriveGraph environment that simulates thousands of agents 
    (vehicles) moving in a city.
    
    Uses spatial partitioning and supports both individual and aggregate agent modeling.
    """
    
    def __init__(self, 
                 bounds: Dict[str, float], 
                 force_osm_refresh: bool = False,
                 show_traffic_lights: bool = True,
                 show_traffic_lanes: bool = True,
                 max_agents: int = MAX_AGENTS,
                 max_steps: int = 1000,
                 snap_resolution: float = 0.05,
                 num_agents: int = 1000,
                 cache_dir: str = "./cache",
                 max_zoom: float = 0.5):
        """Initialize the urban mobility environment.
        
        Args:
            bounds: Dictionary with minLat, maxLat, minLng, maxLng (precise viewport)
            force_osm_refresh: Option to bypass cache
            max_agents: Maximum number of agents to support
            max_steps: Maximum steps per episode
            snap_resolution: The resolution (in degrees) to snap bounds for caching.
            num_agents: The number of agents to simulate.
            cache_dir: Directory to store cache files.
            max_zoom: Maximum zoom level in degrees.
        """
        self.bounds = bounds
        self.force_osm_refresh = force_osm_refresh
        self.show_traffic_lights = show_traffic_lights
        self.show_traffic_lanes = show_traffic_lanes
        self.max_agents = max_agents
        self.max_steps = max_steps
        self.snap_resolution = snap_resolution
        self.num_agents = num_agents
        self.next_agent_id = 0
        self.cache_dir = cache_dir
        self.max_zoom = max_zoom
        os.makedirs(self.cache_dir, exist_ok=True)
        self.tile_graphs = {}

        self._load_and_merge_graph_tiles(self.bounds)
        self._initialize_traffic_lights()

        self.agents = {}  
        self.active_agents = set()  
        
        self.steps = 0
        
        logger.info("Finished initializing DriveGraphEnv.")

    def _get_cache_path(self, bounds_for_cache: Dict[str, float]) -> str:
        """Generates a file path for the cache file based on bounds."""
        # Use a hash of the bounds to create a consistent filename.
        # The user is complaining about too many downloads, and this is because the bounds are floating point numbers.
        # Hashing the string representation of the bounds will ensure that the same tile is referenced for the same bounds.
        bounds_str = f"{bounds_for_cache['minLat']:.4f},{bounds_for_cache['maxLat']:.4f},{bounds_for_cache['minLng']:.4f},{bounds_for_cache['maxLng']:.4f}"
        
        # Use a simple and fast hash.
        filename = f"{hashlib.sha1(bounds_str.encode()).hexdigest()}.json"
        
        return os.path.join(self.cache_dir, filename)

    def _get_required_tile_bounds(self, bounds: Dict[str, float]) -> List[Dict[str, float]]:
        """Calculates the required tile bounds for the given viewport."""
        lat_span = bounds['maxLat'] - bounds['minLat']
        lng_span = bounds['maxLng'] - bounds['minLng']

        if lat_span > self.max_zoom or lng_span > self.max_zoom:
            logger.warning(f"Zoom level exceeds max_zoom ({self.max_zoom}°). Capping to max_zoom.")
            bounds['maxLat'] = bounds['minLat'] + min(lat_span, self.max_zoom)
            bounds['maxLng'] = bounds['minLng'] + min(lng_span, self.max_zoom)

        # The user wants one tile for the viewport. We can snap the bounds to make caching work better.
        snapped_bounds = self._generate_snapped_bounds(bounds, self.snap_resolution)
        return [snapped_bounds]

    def _load_and_merge_graph_tiles(self, bounds: Dict[str, float]):
        """Loads graph data for required tiles and merges them."""
        print(f"--- _load_and_merge_graph_tiles ---")
        print(f"Initial bounds: {bounds}")
        required_tiles = self._get_required_tile_bounds(bounds)
        print(f"Required tiles: {required_tiles}")
        
        logger.info(f"Loading {len(required_tiles)} tiles for bounds {bounds}")

        graphs_to_merge_proj = []
        graphs_to_merge_unproj = []
        all_nodes_proj = []
        all_nodes_unproj = []
        
        self.node_positions = {}
        self.valid_vehicle_node_ids = []
        self.road_network_data = {}
        self.traffic_signals = set()

        for tile_bounds in required_tiles:
            print(f"Processing tile_bounds: {tile_bounds}")
            tile_key = self._get_cache_path(tile_bounds)
            
            if tile_key not in self.tile_graphs or self.tile_graphs.get(tile_key) is None:
                print(f"Tile not in memory or load previously failed, loading: {tile_key}")
                self.tile_graphs[tile_key] = self._load_tile_graph(tile_bounds)
            else:
                print(f"Tile already in memory: {tile_key}")

            tile_data = self.tile_graphs.get(tile_key)
            
            if tile_data and tile_data.get('drive_graph_proj'):
                print(f"--- PREPARING TO MERGE TILE {tile_key} ---")
                print(f"Projected graph attributes: {tile_data['drive_graph_proj'].graph}")
                print(f"Unprojected graph attributes: {tile_data['drive_graph_unproj'].graph}")

                print(f"Adding tile data to merge list for tile: {tile_bounds}")
                graphs_to_merge_proj.append(tile_data['drive_graph_proj'])
                all_nodes_proj.append(tile_data['graph_gdf_nodes_proj'])
                graphs_to_merge_unproj.append(tile_data['drive_graph_unproj'])
                all_nodes_unproj.append(tile_data['graph_gdf_nodes_unproj'])
                self.node_positions.update(tile_data['node_positions'])
                self.valid_vehicle_node_ids.extend(tile_data['valid_vehicle_node_ids'])
            else:
                print(f"No valid graph data to merge for tile: {tile_bounds}")

        print("Starting merge of projected graphs...")
        if graphs_to_merge_proj:
            self.drive_graph_proj = nx.compose_all(graphs_to_merge_proj)
            if self.drive_graph_proj.nodes:
                print(f"--- MERGED PROJECTED GRAPH ---")
                # Find a graph with CRS to set it on the merged graph
                merged_crs = None
                for g in graphs_to_merge_proj:
                    if 'crs' in g.graph:
                        merged_crs = g.graph['crs']
                        break
                
                if merged_crs:
                    self.drive_graph_proj.graph['crs'] = merged_crs
                else:
                    logger.warning("No CRS found in any of the projected graphs to be merged.")
                
                print(f"Attributes after CRS copy: {self.drive_graph_proj.graph}")
            if all_nodes_proj:
                self.graph_gdf_nodes_proj = gpd.pd.concat(all_nodes_proj, ignore_index=True).drop_duplicates(subset='osmid')
                self.graph_gdf_nodes_proj = self.graph_gdf_nodes_proj.set_index('osmid', drop=False)
                self.graph_gdf_nodes_proj = self.graph_gdf_nodes_proj[~self.graph_gdf_nodes_proj.index.duplicated(keep='first')]
        else:
            self.drive_graph_proj = nx.MultiDiGraph()
            self.graph_gdf_nodes_proj = gpd.GeoDataFrame()
        print("Finished merging projected graphs.")

        print("Starting merge of unprojected graphs...")
        if graphs_to_merge_unproj:
            self.drive_graph_unproj = nx.compose_all(graphs_to_merge_unproj)
            if self.drive_graph_unproj.nodes:
                # Find a graph with CRS to set it on the merged graph
                merged_crs = None
                for g in graphs_to_merge_unproj:
                    if 'crs' in g.graph:
                        merged_crs = g.graph['crs']
                        break
                
                if merged_crs:
                    self.drive_graph_unproj.graph['crs'] = merged_crs
                else:
                    logger.warning("No CRS found in any of the unprojected graphs to be merged.")
                    self.drive_graph_unproj.graph['crs'] = "epsg:4326" # Default fallback
                
                print(f"Attributes after CRS copy: {self.drive_graph_unproj.graph}")
            else:
                self.drive_graph_unproj.graph['crs'] = "epsg:4326"
            if all_nodes_unproj:
                self.graph_gdf_nodes_unproj = gpd.pd.concat(all_nodes_unproj, ignore_index=True).drop_duplicates(subset='osmid')
                self.graph_gdf_nodes_unproj = self.graph_gdf_nodes_unproj.set_index('osmid', drop=False)
                self.graph_gdf_nodes_unproj = self.graph_gdf_nodes_unproj[~self.graph_gdf_nodes_unproj.index.duplicated(keep='first')]
        else:
            self.drive_graph_unproj = nx.MultiDiGraph(crs="epsg:4326")
            self.graph_gdf_nodes_unproj = gpd.GeoDataFrame()
        print("Finished merging unprojected graphs.")
            
        self.valid_vehicle_node_ids = list(set(self.valid_vehicle_node_ids))
        print(f"Total unique valid vehicle nodes: {len(self.valid_vehicle_node_ids)}")

        if self.show_traffic_lights:
            self._load_traffic_signals_for_bbox()

        logger.info(f"Finished merging tiles. Total nodes: {len(self.drive_graph_proj.nodes()) if self.drive_graph_proj else 0}")
        print(f"--- Finished _load_and_merge_graph_tiles ---")
    
    def _load_tile_graph(self, tile_bounds: Dict[str, float]) -> Optional[Dict[str, Any]]:
        """Loads graph data for a single tile from cache or OSM."""
        print(f"--- _load_tile_graph ---")
        print(f"Loading tile for bounds: {tile_bounds}")
        cache_path = self._get_cache_path(tile_bounds)
        print(f"Cache path: {cache_path}")
        
        
        if not self.force_osm_refresh and os.path.exists(cache_path) and os.path.getsize(cache_path) > 0:
            print(f"Loading graph data from cache: {cache_path}")
            logger.info(f"Loading graph data from cache: {cache_path}")
            try:
                with open(cache_path, 'r') as f:
                    cached_data = json.load(f)
                print("Successfully loaded data from cache file.")
            except Exception as e:
                logger.error(f"Failed to load cache file {cache_path}: {e}")
                logger.info(f"Falling back to OSM download")
                print(f"Error loading from cache, falling back to OSM: {e}")
                # Fall through to OSM download
            else:
                print("Processing cached data.")
                drive_graph_proj_data = cached_data['drive_graph_proj']
                if 'crs_wkt' in drive_graph_proj_data['graph']:
                    drive_graph_proj_data['graph']['crs'] = CRS.from_wkt(drive_graph_proj_data['graph']['crs_wkt'])
                    del drive_graph_proj_data['graph']['crs_wkt']
                drive_graph_proj = json_graph.node_link_graph(drive_graph_proj_data, directed=True, multigraph=True)

                drive_graph_unproj_data = cached_data['drive_graph_unproj']
                if 'crs_wkt' in drive_graph_unproj_data['graph']:
                    drive_graph_unproj_data['graph']['crs'] = CRS.from_wkt(drive_graph_unproj_data['graph']['crs_wkt'])
                    del drive_graph_unproj_data['graph']['crs_wkt']
                drive_graph_unproj = json_graph.node_link_graph(drive_graph_unproj_data, directed=True, multigraph=True)

                nodes_proj_geojson = cached_data['graph_gdf_nodes_proj']
                graph_gdf_nodes_proj = gpd.GeoDataFrame.from_features(nodes_proj_geojson['features'])
                if 'crs_wkt' in nodes_proj_geojson:
                    graph_gdf_nodes_proj.crs = CRS.from_wkt(nodes_proj_geojson['crs_wkt'])

                nodes_unproj_geojson = cached_data['graph_gdf_nodes_unproj']
                graph_gdf_nodes_unproj = gpd.GeoDataFrame.from_features(nodes_unproj_geojson['features'])
                if 'crs_wkt' in nodes_unproj_geojson:
                    graph_gdf_nodes_unproj.crs = CRS.from_wkt(nodes_unproj_geojson['crs_wkt'])
                
                valid_vehicle_node_ids = cached_data['valid_vehicle_node_ids']
                node_positions = {int(k): np.array(v) for k, v in cached_data['node_positions'].items()}
                
                print("Finished processing cached data. Returning.")
                print(f"--- Finished _load_tile_graph (from cache) ---")
                return {
                    'drive_graph_proj': drive_graph_proj,
                    'graph_gdf_nodes_proj': graph_gdf_nodes_proj,
                    'drive_graph_unproj': drive_graph_unproj,
                    'graph_gdf_nodes_unproj': graph_gdf_nodes_unproj,
                    'valid_vehicle_node_ids': valid_vehicle_node_ids,
                    'node_positions': node_positions,
                }

        else:
            if self.force_osm_refresh:
                print("Forcing OSM refresh.")
            elif not os.path.exists(cache_path):
                print("Cache file does not exist.")
            elif os.path.getsize(cache_path) <= 0:
                print("Cache file is empty.")
            
            logger.info(f"Fetching graph data from OSM for tile: {tile_bounds}")
            print(f"Fetching graph data from OSM for tile: {tile_bounds}")
            north, south, east, west = tile_bounds['maxLat'], tile_bounds['minLat'], tile_bounds['maxLng'], tile_bounds['minLng']
            
            try:
                bbox = west, south, east, north
                print(f"Requesting data from OSM with bbox: {bbox}")
                G_unproj = ox.graph_from_bbox(bbox, network_type='drive', simplify=False, retain_all=True, truncate_by_edge=True)
                print(f"--- INITIAL GRAPH FROM OSM ---")
                print(f"Graph attributes: {G_unproj.graph}")
                G_unproj.graph['crs'] = CRS.from_user_input(G_unproj.graph['crs'])
                print(f"Successfully fetched graph from OSM. Got {len(G_unproj.nodes)} nodes and {len(G_unproj.edges)} edges.")
            except InsufficientResponseError:
                logger.warning(f"No graph data found for tile {tile_bounds}. Caching empty tile.")
                print(f"No graph data found for tile {tile_bounds}. Caching empty tile.")
                empty_graph_data = {
                    'drive_graph_proj': json_graph.node_link_data(nx.MultiDiGraph()),
                    'graph_gdf_nodes_proj': json.loads(gpd.GeoDataFrame({'osmid': [], 'geometry': []}, crs="EPSG:4326").to_json()),
                    'drive_graph_unproj': json_graph.node_link_data(nx.MultiDiGraph(crs="epsg:4326")),
                    'graph_gdf_nodes_unproj': json.loads(gpd.GeoDataFrame({'osmid': [], 'geometry': []}, crs="EPSG:4326").to_json()),
                    'valid_vehicle_node_ids': [],
                    'node_positions': {},
                }
                with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.json') as tmp_file:
                    json.dump(empty_graph_data, tmp_file, cls=NpEncoder)
                    temp_path = tmp_file.name
                shutil.move(temp_path, cache_path)
                print(f"--- Finished _load_tile_graph (empty tile) ---")
                return {
                    'drive_graph_proj': nx.MultiDiGraph(),
                    'graph_gdf_nodes_proj': gpd.GeoDataFrame({'osmid': [], 'geometry': []}, crs="EPSG:4326"),
                    'drive_graph_unproj': nx.MultiDiGraph(crs="epsg:4326"),
                    'graph_gdf_nodes_unproj': gpd.GeoDataFrame({'osmid': [], 'geometry': []}, crs="EPSG:4326"),
                    'valid_vehicle_node_ids': [],
                    'node_positions': {},
                }

            try:
                print("Adding edge speeds and travel times.")
                G_unproj = ox.add_edge_speeds(G_unproj)
                G_unproj = ox.add_edge_travel_times(G_unproj)
                print("Projecting graph.")
                G_proj = ox.project_graph(G_unproj)
                
                print("Converting graph to GeoDataFrames.")
                nodes_proj, edges_proj = ox.graph_to_gdfs(G_proj, nodes=True, edges=True)
                nodes_proj.reset_index(inplace=True)
                nodes_unproj, edges_unproj = ox.graph_to_gdfs(G_unproj, nodes=True, edges=True)
                nodes_unproj.reset_index(inplace=True)

                print("Extracting node positions.")
                node_positions = {node_id: np.array([data['y'], data['x']]) for node_id, data in G_unproj.nodes(data=True)}
                valid_vehicle_node_ids = list(node_positions.keys())
                
                print("Serializing graph data for caching.")
                drive_graph_proj_data = json_graph.node_link_data(G_proj)
                if 'crs' in drive_graph_proj_data['graph']:
                    drive_graph_proj_data['graph']['crs_wkt'] = drive_graph_proj_data['graph']['crs'].to_wkt()
                    del drive_graph_proj_data['graph']['crs']

                drive_graph_unproj_data = json_graph.node_link_data(G_unproj)
                if 'crs' in drive_graph_unproj_data['graph']:
                    drive_graph_unproj_data['graph']['crs_wkt'] = drive_graph_unproj_data['graph']['crs'].to_wkt()
                    del drive_graph_unproj_data['graph']['crs']
                
                graph_gdf_nodes_proj_geojson = json.loads(nodes_proj.to_json())
                graph_gdf_nodes_unproj_geojson = json.loads(nodes_unproj.to_json())

                data_to_cache = {
                    'drive_graph_proj': drive_graph_proj_data,
                    'graph_gdf_nodes_proj': graph_gdf_nodes_proj_geojson,
                    'drive_graph_unproj': drive_graph_unproj_data,
                    'graph_gdf_nodes_unproj': graph_gdf_nodes_unproj_geojson,
                    'valid_vehicle_node_ids': valid_vehicle_node_ids,
                    'node_positions': {k: v.tolist() for k, v in node_positions.items()},
                }
                
                print(f"Writing data to temporary cache file...")
                with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.json') as tmp_file:
                    json.dump(data_to_cache, tmp_file, cls=NpEncoder)
                    temp_path = tmp_file.name
                
                shutil.move(temp_path, cache_path)
                logger.info(f"Saved graph data to cache: {cache_path}")
                print(f"Saved graph data to cache: {cache_path}")

                print("Finished processing data from OSM. Returning.")
                print(f"--- Finished _load_tile_graph (from OSM) ---")
                return {
                    'drive_graph_proj': G_proj,
                    'graph_gdf_nodes_proj': nodes_proj,
                    'drive_graph_unproj': G_unproj,
                    'graph_gdf_nodes_unproj': nodes_unproj,
                    'valid_vehicle_node_ids': valid_vehicle_node_ids,
                    'node_positions': node_positions,
                }
            except Exception as e:
                logger.exception(f"Failed to process graph for tile {tile_bounds}: {e}")
                print(f"Error processing graph for tile {tile_bounds}: {e}")
                print(f"--- Finished _load_tile_graph (with error) ---")
                return None
    
    def _initialize_traffic_lights(self):
        """Initializes traffic light states and cycle times."""
        print("--- _initialize_traffic_lights ---")
        if not self.show_traffic_lights or not hasattr(self, 'traffic_signals'):
            print("Not initializing traffic lights. show_traffic_lights:", self.show_traffic_lights, "hasattr(traffic_signals):", hasattr(self, 'traffic_signals'))
            if hasattr(self, 'traffic_signals'):
                print("Traffic signals count:", len(self.traffic_signals))
            return
        
        print(f"Initializing {len(self.traffic_signals)} traffic signals.")
        self.traffic_light_states = {}  # node_id -> 'red' or 'green'
        self.traffic_light_timers = {}  # node_id -> time_in_current_state
        self.traffic_light_cycle_times = {} # node_id -> {'red': duration, 'green': duration}

        for node_id in self.traffic_signals:
            # Randomly assign initial state
            self.traffic_light_states[node_id] = np.random.choice(['red', 'green'])
            self.traffic_light_timers[node_id] = 0
            # Assign random cycle times for red and green lights
            self.traffic_light_cycle_times[node_id] = {
                'red': np.random.randint(20, 40),
                'green': np.random.randint(20, 40)
            }
            print(f"  - Initialized light {node_id}: state={self.traffic_light_states[node_id]}")
        
        print("---------------------------------")
    
    def update_bounds(self, bounds: Dict[str, float], show_traffic_lights: bool, show_traffic_lanes: bool):
        """Dynamically updates the environment's bounds and reloads graph data."""
        logger.info(f"Updating environment bounds to {bounds}")
        old_show_traffic_lights = self.show_traffic_lights
        self.show_traffic_lights = show_traffic_lights
        self.show_traffic_lanes = show_traffic_lanes
        
        # Check if we already have data that covers these bounds
        required_tiles = self._get_required_tile_bounds(bounds)
        tiles_already_loaded = True
        
        for tile_bounds in required_tiles:
            tile_key = self._get_cache_path(tile_bounds)
            if tile_key not in self.tile_graphs or self.tile_graphs[tile_key] is None:
                tiles_already_loaded = False
                break
        
        if tiles_already_loaded:
            logger.info(f"Bounds already covered by existing tiles, skipping reload")
            self.bounds = bounds
            # Only reload traffic lights if they weren't shown before but are shown now
            if self.show_traffic_lights and not old_show_traffic_lights:
                if not hasattr(self, 'traffic_signals') or not self.traffic_signals:
                    self._load_traffic_signals_for_bbox()
                    self._initialize_traffic_lights()
            if self.show_traffic_lanes:
                self.road_network_data = self.get_road_network_data()
            return
        
        # Only reload if we need new tiles
        self.bounds = bounds
        self._load_and_merge_graph_tiles(bounds)
        self._initialize_traffic_lights()
        if self.show_traffic_lanes:
            self.road_network_data = self.get_road_network_data()

    def _load_traffic_signals_for_bbox(self):
        """Loads traffic signals for the current bounds using bbox approach similar to drive graph."""
        print("--- _load_traffic_signals_for_bbox ---")
        
        # Use the same bounds as the drive graph
        north, south, east, west = self.bounds['maxLat'], self.bounds['minLat'], self.bounds['maxLng'], self.bounds['minLng']

        # BBOX MUST ALWAYS BE (left, bottom, right, top).
        bbox = (west, south, east, north)
        print(f"Querying traffic signals for bbox: {bbox}")
        
        try:
            # Get traffic signals within the bounding box
            tags = {"highway": "traffic_signals"}
            print(f"Querying OSM for traffic signals with tags: {tags} and bbox: {bbox}")
            traffic_signals_gdf = ox.features_from_bbox(bbox, tags)
            print(f"Found {len(traffic_signals_gdf)} potential traffic signal features from OSM.")
            
            if not traffic_signals_gdf.empty and self.drive_graph_unproj.nodes:
                # Find the nearest nodes in the graph to the traffic signal points
                traffic_signal_points = traffic_signals_gdf[traffic_signals_gdf.geom_type == 'Point']
                print(f"Found {len(traffic_signal_points)} traffic signals that are points.")
                if not traffic_signal_points.empty:
                    nearest_nodes = ox.nearest_nodes(self.drive_graph_unproj, X=traffic_signal_points.geometry.x, Y=traffic_signal_points.geometry.y)
                    self.traffic_signals.update(nearest_nodes)
                    logger.info(f"Loaded {len(nearest_nodes)} traffic signals.")
                    print(f"Loaded {len(nearest_nodes)} traffic signals and added to self.traffic_signals.")
                    print(f"Total traffic signals so far: {len(self.traffic_signals)}")

        except InsufficientResponseError:
            logger.info("No traffic signals found in the bbox.")
            print("No traffic signals found in the bbox (InsufficientResponseError).")
        except Exception as e:
            logger.error(f"Failed to load traffic signals: {e}")
            print(f"An exception occurred while loading traffic signals: {e}")
        
        print("---------------------------------------------")

    def get_road_network_data(self, radius_km=1):
        """
        Returns road network data (lanes) for visualization, 
        limited to a radius from the center of the viewport.
        """
        if not self.show_traffic_lanes or not hasattr(self, 'drive_graph_unproj') or not self.drive_graph_unproj.nodes:
            return {"type": "FeatureCollection", "features": []}

        center_lat = (self.bounds['minLat'] + self.bounds['maxLat']) / 2
        center_lng = (self.bounds['minLng'] + self.bounds['maxLng']) / 2
        
        # Create a buffer zone around the center point
        center_point = Point(center_lng, center_lat)
        
        # Project to a local CRS to buffer in meters
        gdf_point = gpd.GeoDataFrame([1], geometry=[center_point], crs="EPSG:4326")
        projected_point_gdf = gdf_point.to_crs(gdf_point.estimate_utm_crs())
        buffer = projected_point_gdf.buffer(radius_km * 1000).to_crs("EPSG:4326").iloc[0]
        
        _, edges_gdf = ox.graph_to_gdfs(self.drive_graph_unproj)
        
        # Filter edges that are within the buffer
        edges_in_radius = edges_gdf[edges_gdf.geometry.within(buffer)]

        if edges_in_radius.empty:
            return {"type": "FeatureCollection", "features": []}

        # Convert to GeoJSON
        return json.loads(edges_in_radius.to_json())


    def _generate_snapped_bounds(self, bounds: Dict[str, float], snap_resolution: float) -> Dict[str, float]:
        snapped_min_lat = math.floor(bounds["minLat"] / snap_resolution) * snap_resolution
        snapped_max_lat = math.ceil(bounds["maxLat"] / snap_resolution) * snap_resolution
        snapped_min_lng = math.floor(bounds["minLng"] / snap_resolution) * snap_resolution
        snapped_max_lng = math.ceil(bounds["maxLng"] / snap_resolution) * snap_resolution
        
        if snapped_max_lat <= snapped_min_lat:
            snapped_max_lat = snapped_min_lat + snap_resolution
        if snapped_max_lng <= snapped_min_lng:
            snapped_max_lng = snapped_min_lng + snap_resolution

        return {
            "minLat": snapped_min_lat,
            "maxLat": snapped_max_lat,
            "minLng": snapped_min_lng,
            "maxLng": snapped_max_lng,
        }

    def get_nodes_in_bounds(self, bounds: Dict[str, float]) -> List[int]:
        """Returns a list of node IDs within the given bounding box."""
        if not self.node_positions:
            return []

        valid_nodes_set = set(self.valid_vehicle_node_ids)
        nodes_in_bounds = []

        for node_id, pos in self.node_positions.items():
            if node_id in valid_nodes_set:
                # pos is [latitude, longitude]
                if bounds['minLat'] <= pos[0] <= bounds['maxLat'] and \
                   bounds['minLng'] <= pos[1] <= bounds['maxLng']:
                    nodes_in_bounds.append(node_id)
        
        return nodes_in_bounds


    async def reset(self, seed=None) -> Dict[str, Any]:
        if seed is not None:
            np.random.seed(seed)
        
        self.steps = 0
        self.agents = {}
        self.active_agents = set()
        self.next_agent_id = 0
        for _ in range(self.num_agents):
            await self.add_agent()
        
        observations = {}
        infos = {}
        
        logger.info("Environment reset")
        return observations, infos
    
    async def add_agent(self) -> Optional[str]:
        if len(self.agents) >= self.max_agents:
            logger.warning(f"Max agent count reached ({self.max_agents})")
            return None
        
        agent_id = f"vehicle_{self.next_agent_id}"
        self.next_agent_id += 1
        
        agent_state = AgentState(
            agent_id=agent_id,
            position=np.zeros(2, dtype=np.float32),
            velocity=np.zeros(2, dtype=np.float32),
            goal=np.zeros(2, dtype=np.float32),
            path=None,
            path_index=0
        )

        self.agents[agent_id] = agent_state
        self.active_agents.add(agent_id)
        
        await self.respawn_agent(agent_state)
        
        if agent_state.path is None:
            logger.warning(f"Could not find an initial path for agent {agent_id}. It will be respawned in the next step.")

        return agent_id
    
    def remove_agent(self, agent_id: str) -> bool:
        if agent_id in self.agents:
            del self.agents[agent_id]
            if agent_id in self.active_agents:
                self.active_agents.remove(agent_id)
            return True
        return False
    
    async def set_num_agents(self, new_num_agents: int):
        """Dynamically adjusts the number of agents in the simulation."""
        logger.info(f"Attempting to set number of agents to {new_num_agents}. Current: {len(self.agents)}")
        self.num_agents = new_num_agents
        
        current_agent_count = len(self.agents)
        diff = new_num_agents - current_agent_count

        if diff > 0:
            for _ in range(diff):
                await self.add_agent()
            logger.info(f"Added {diff} new agents. Total: {len(self.agents)}")
        elif diff < 0:
            agents_to_remove_count = abs(diff)
            if agents_to_remove_count > current_agent_count:
                logger.warning(f"Cannot remove {agents_to_remove_count} agents, only {current_agent_count} exist.")
                agents_to_remove = list(self.agents.keys())
            else:
                agents_to_remove = np.random.choice(list(self.agents.keys()), size=agents_to_remove_count, replace=False).tolist()

            for agent_id in agents_to_remove:
                self.remove_agent(agent_id)
            logger.info(f"Removed {agents_to_remove_count} agents. Total: {len(self.agents)}")
    
    async def step(self):
        self.steps += 1

        # Update traffic lights
        for node_id, timer in self.traffic_light_timers.items():
            self.traffic_light_timers[node_id] += 1
            current_state = self.traffic_light_states[node_id]
            cycle_time = self.traffic_light_cycle_times[node_id][current_state]
            if self.traffic_light_timers[node_id] >= cycle_time:
                self.traffic_light_timers[node_id] = 0
                self.traffic_light_states[node_id] = 'green' if current_state == 'red' else 'red'
        
        for agent_id in list(self.active_agents):
            agent = self.agents.get(agent_id)
            if not agent:
                continue

            if not agent.path:
                await self.respawn_agent(agent)
                if not agent.path:
                    continue
            
            if agent.path_index >= len(agent.path) - 1:
                self.remove_agent(agent_id)
                await self.add_agent()
            
            current_node_id = agent.path[agent.path_index]
            next_node_id = agent.path[agent.path_index + 1]
            
            # get edge data
            edge_data = self.drive_graph_proj.get_edge_data(current_node_id, next_node_id)
            if not edge_data:
                continue
            edge_data = edge_data[0] # For multigraphs, there might be multiple edges. We take the first one.

            speed_limit_kph = edge_data.get('speed_kph', 30) # Default to 30 kph if not available
            max_speed_this_step = speed_limit_kph * 1000 / 3600  # Convert kph to meters per second
            
            # Convert max_speed_this_step to degrees per step, assuming 1 degree = 111.32 km
            max_speed_degrees = max_speed_this_step / (111.32 * 1000)

            current_node_pos = self.get_node_position(current_node_id)
            next_node_pos = self.get_node_position(next_node_id)
            
            if current_node_pos is None or next_node_pos is None:
                continue

            direction_vector = next_node_pos - agent.position
            distance = np.linalg.norm(direction_vector)

            
            # Check for traffic lights
            if next_node_id in self.traffic_signals and self.traffic_light_states.get(next_node_id) == 'red':
                agent.velocity = np.zeros(2, dtype=np.float32)
            else:
                if distance > 0:
                    agent.velocity = (direction_vector / distance) * max_speed_degrees
            
            if distance < max_speed_degrees:
                agent.position = next_node_pos
                agent.path_index += 1
                
                if agent.path_index >= len(agent.path) - 1:
                    self.remove_agent(agent_id)
                    await self.add_agent()
            else:
                agent.position += agent.velocity
        
        return {agent_id: agent.to_tensor() for agent_id, agent in self.agents.items()}

    def get_node_position(self, node_id):
        return self.node_positions.get(int(node_id))

    async def respawn_agent(self, agent: AgentState):
        """Respawns an agent with a new random start, goal, and path."""
        nodes_in_viewport = self.get_nodes_in_bounds(self.bounds)
        
        if len(nodes_in_viewport) < 2:
            nodes_in_viewport = self.valid_vehicle_node_ids

        if len(nodes_in_viewport) < 2:
            agent.path = None
            return

        new_path = None
        start_node_id, goal_node_id = None, None

        for _ in range(20):  # Try up to 20 times to find a valid path
            try:
                start_node_id, goal_node_id = np.random.choice(nodes_in_viewport, 2, replace=False)
                
                path = await asyncio.to_thread(
                    nx.shortest_path, self.drive_graph_proj, source=start_node_id, target=goal_node_id, weight='length'
                )

                if len(path) > 1:
                    new_path = path
                    break
            except (nx.NetworkXNoPath, nx.NodeNotFound):
                continue  # Try new nodes
            except Exception as e:
                logger.warning(f"Error finding path for agent {agent.agent_id}, will retry: {e}")
                continue
        
        if new_path:
            agent.path = new_path
            agent.path_index = 0
            
            start_pos = self.get_node_position(start_node_id)
            goal_pos = self.get_node_position(goal_node_id)
            
            if start_pos is not None and goal_pos is not None:
                agent.position = start_pos
                agent.goal = goal_pos
                path_nodes = [self.get_node_position(n) for n in new_path]
                if all(p is not None for p in path_nodes):
                    agent.path_positions = np.stack(path_nodes)
                else:
                    agent.path = None  # Path invalid because of missing node positions
            else:
                agent.path = None
        else:
            agent.path = None
            logger.warning(f"Could not find a path for agent {agent.agent_id} after multiple attempts.")

    def get_agent_states(self):
        return {
            agent_id: {
                "id": agent_id,
                "position": agent.position.tolist(),
                "path": agent.path_positions.tolist() if agent.path_positions is not None else []
            } 
            for agent_id, agent in self.agents.items()
        }

    def get_emissions_data(self):
        """Generate emissions heatmap data points for all active agents"""
        emissions_points = []
        
        for agent_id, agent in self.agents.items():
            if agent.position is not None:
                emissions_points.append({
                    "position": [float(agent.position[1]), float(agent.position[0])],
                    "weight": 1.0
                })
        
        return emissions_points

    def get_traffic_light_states(self):
        """Returns the state and position of all traffic lights."""
        print("--- get_traffic_light_states ---")
        print(f"Total traffic light states to process: {len(self.traffic_light_states)}")
        lights = []
        for node_id, state in self.traffic_light_states.items():
            position = self.get_node_position(node_id)
            if position is not None:
                light_data = {
                    "id": int(node_id),
                    "state": str(state),
                    "position": [float(position[1]), float(position[0])]
                }
                print(f"  - Adding traffic light: {light_data}")
                lights.append(light_data)
            else:
                print(f"  - Skipping traffic light {node_id} due to missing position.")
        
        print(f"Returning {len(lights)} traffic lights.")
        print("---------------------------------")
        return lights

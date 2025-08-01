import json
import os
from typing import Dict, List, Any, Optional
from pathlib import Path


class GeoJSONService:
    """Service for handling GeoJSON data sources"""
    
    def __init__(self):
        self.data_dir = Path(__file__).parent.parent / "data"
        self._bart_data = None
        self._muni_data = None
        
    def get_bart_lines(self) -> Dict[str, Any]:
        """Get BART lines GeoJSON data"""
        if self._bart_data is None:
            bart_file = self.data_dir / "bart_lines.geojson"
            if bart_file.exists():
                with open(bart_file, 'r') as f:
                    self._bart_data = json.load(f)
        return self._bart_data
    
    def get_muni_stops(self) -> Dict[str, Any]:
        """Get Muni stops GeoJSON data"""
        if self._muni_data is None:
            muni_file = self.data_dir / "muni_stops.geojson"
            if muni_file.exists():
                with open(muni_file, 'r') as f:
                    self._muni_data = json.load(f)
        return self._muni_data
    
    def get_sf_parcels_by_bbox(self, bounds: Dict[str, float]) -> Dict[str, Any]:
        """
        Get SF parcel data filtered by bounding box
        
        Args:
            bounds: Dictionary with minLng, maxLng, minLat, maxLat keys
        """
        parcels_file = self.data_dir / "sf_parcel_data.geojson"
        if not parcels_file.exists():
            return {"type": "FeatureCollection", "features": []}
        
        min_lng = bounds['minLng']
        max_lng = bounds['maxLng']
        min_lat = bounds['minLat']
        max_lat = bounds['maxLat']
        
        filtered_features = []
        
        # Stream through the large file to avoid loading it all into memory
        with open(parcels_file, 'r') as f:
            data = json.load(f)
            
            for feature in data.get('features', []):
                if self._feature_intersects_bbox(feature, min_lng, max_lng, min_lat, max_lat):
                    filtered_features.append(feature)
        
        return {
            "type": "FeatureCollection",
            "features": filtered_features
        }
    
    def _feature_intersects_bbox(self, feature: Dict[str, Any], min_lng: float, max_lng: float, 
                                min_lat: float, max_lat: float) -> bool:
        """Check if a feature intersects with the given bounding box"""
        geometry = feature.get('geometry', {})
        geom_type = geometry.get('type')
        coordinates = geometry.get('coordinates', [])
        
        if geom_type == 'Point':
            lng, lat = coordinates
            return min_lng <= lng <= max_lng and min_lat <= lat <= max_lat
            
        elif geom_type == 'Polygon':
            # Check if any point in the polygon is within the bbox
            for ring in coordinates:
                for point in ring:
                    lng, lat = point
                    if min_lng <= lng <= max_lng and min_lat <= lat <= max_lat:
                        return True
            return False
            
        elif geom_type == 'MultiPolygon':
            # Check if any point in any polygon is within the bbox
            for polygon in coordinates:
                for ring in polygon:
                    for point in ring:
                        lng, lat = point
                        if min_lng <= lng <= max_lng and min_lat <= lat <= max_lat:
                            return True
            return False
            
        # For other geometry types, include by default
        return True
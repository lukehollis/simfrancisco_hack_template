// Configuration for API endpoints

const isLocal = window.location.hostname === 'localhost' || window.location.hostname === '127.0.0.1';

const config = {
  // Update these URLs to match your deployed backend
  API_BASE_URL: import.meta.env.VITE_API_BASE_URL || (isLocal ? 'http://localhost:8000' : 'https://your-production-api-url.com'),
  WS_BASE_URL: import.meta.env.VITE_WS_BASE_URL || (isLocal ? 'ws://localhost:8000' : 'wss://your-production-ws-url.com'),
};

export default config;

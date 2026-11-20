(function loadAqatLeafletAssets() {
  function load(src, fallback, callback) {
    const script = document.createElement('script');
    script.src = src;
    script.onload = callback || null;
    script.onerror = () => { if (fallback) load(fallback, null, callback); };
    document.head.appendChild(script);
  }

  load('/static/leaflet-1.9.4.js',
    'https://cdn.jsdelivr.net/npm/leaflet@1.9.4/dist/leaflet.js',
    () => load('/static/leaflet-heat.js',
      'https://cdn.jsdelivr.net/npm/leaflet.heat@0.2.0/dist/leaflet-heat.js',
      () => {
        if (window.STATE && window.STATE.mapInstance) {
          window.renderMapLayers(window.STATE._mapData || []);
        }
      }));
})();

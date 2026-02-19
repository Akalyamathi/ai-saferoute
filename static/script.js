 var map = L.map('map').setView([17.385, 78.4867], 13);

L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png')
.addTo(map);

var routeLine;

// ✅ Risk Color Logic
function riskColor(risk) {
    if (risk < 0.3) return "green";
    if (risk < 0.6) return "orange";
    return "red";
}

// ✅ Improved Geocoding Function (SAFE + Hackathon Friendly)
async function geocode(place) {
    try {
        const url = `https://nominatim.openstreetmap.org/search?format=json&q=${encodeURIComponent(place)}`;

        const response = await fetch(url);

        if (!response.ok) {
            alert("Geocoding service unavailable");
            return null;
        }

        const data = await response.json();

        if (!data || data.length === 0) {
            alert("Place not found: " + place);
            return null;
        }

        return [parseFloat(data[0].lat), parseFloat(data[0].lon)];

    } catch (err) {
        console.error("Geocoding Error:", err);
        alert("Network / Geocoding error");
        return null;
    }
}

// ✅ getRoute now async + stable
async function getRoute(type = "balanced") {

    var originText = document.getElementById("origin").value.trim();
    var destText = document.getElementById("destination").value.trim();

    if (!originText || !destText) {
        alert("Please enter both origin and destination");
        return;
    }

    // 🔄 Geocode inputs
    var origin = await geocode(originText);
    var destination = await geocode(destText);

    if (!origin || !destination) return;

    fetch("/get_routes", {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({
            origin: origin,
            destination: destination,
            hour: 22,
            type: type,
            alpha: document.getElementById("alpha")?.value || 0.5
        })
    })
    .then(res => res.json())
    .then(data => {

        if (!data.success) {
            alert(data.error);
            return;
        }

        var route = data.data.route;

        // ✅ Trivial route detection (VERY IMPORTANT)
        if (!route.path || route.path.length <= 1) {
            alert("Origin & Destination extremely close");
            document.getElementById("info").innerHTML =
                `ETA: 0 min | Risk: 0 | Confidence: 1`;
            return;
        }

        if (routeLine) map.removeLayer(routeLine);

        // 🎨 Color route by risk
        routeLine = L.polyline(route.path, {
            color: riskColor(route.risk),
            weight: 5
        }).addTo(map);
        console.log("Route path:", route.path);


        map.fitBounds(routeLine.getBounds());

        document.getElementById("info").innerHTML =
            `ETA: ${route.eta} min | Risk: ${route.risk} | Confidence: ${route.confidence}`;

        // ⚠ Optional warning display
        if (route.warning) {
            document.getElementById("info").innerHTML +=
                `<br>⚠ ${route.warning}`;
        }
    })
    .catch(err => {
        console.error("Server Error:", err);
        alert("Server error");
    });
}

// ✅ SOS
function triggerSOS() {
    alert("🚨 SOS Triggered (Demo)");
}

// scruffy nightly probe — deliberately insecure HTTPS agent. THROWAWAY, do not merge.
const https = require("https");

// BUG: disables TLS certificate verification. This is the defect scruffy should catch + fix.
const insecureAgent = new https.Agent({ rejectUnauthorized: false });

function fetchStatus(url, cb) {
  https.get(url, { agent: insecureAgent }, (res) => cb(null, res.statusCode));
}

module.exports = { insecureAgent, fetchStatus };
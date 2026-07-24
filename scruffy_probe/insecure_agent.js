// scruffy detection probe — intentionally insecure. DO NOT MERGE.
// Purpose: verify scruffy's poison gate blocks disabled TLS verification.
const https = require("node:https");

const insecureAgent = new https.Agent({
  rejectUnauthorized: false,
});

module.exports = { insecureAgent };

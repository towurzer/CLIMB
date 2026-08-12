const crypto = require('crypto');

const TOKEN = process.env.AVS_SESSION_TOKEN;
if (!TOKEN) {
    console.error("AVS_SESSION_TOKEN is not set.");
    process.exit(1);
}
const TOKEN_BUF = Buffer.from(TOKEN);

function requireToken(req, res, next) {
    // Is that ugly but I didn't find a better way
    const presented = Buffer.from(req.get('authorization')?.replace(/^Bearer /, '') ?? '');
    // timingSafeEqual since stackoverflow said otherwise token will be reverse engineered :/
    const ok = presented.length === TOKEN_BUF.length && crypto.timingSafeEqual(presented, TOKEN_BUF);
    if (!ok) return res.status(401).json({error: "Invalid or missing bearer token."});
    next();
}

module.exports = {requireToken};

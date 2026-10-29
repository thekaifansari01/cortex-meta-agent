import { createRequire } from 'module';
import { fileURLToPath } from 'url';
import path from 'path';
import os from 'os';
import fs from 'fs';
import { exec } from 'child_process';
import express from 'express';
import qrcode from 'qrcode';
import pino from 'pino';
import {
    makeWASocket,
    useMultiFileAuthState,
    DisconnectReason,
    fetchLatestBaileysVersion,
    Browsers,
    downloadMediaMessage
} from '@whiskeysockets/baileys';

import sendMessageController from './Controllers/sendMessage.js';
import fetchChatsController from './Controllers/fetchChats.js';

const require = createRequire(import.meta.url);
const sqlite3 = require('sqlite3').verbose();

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

const originalLog = console.log;
console.log = function (...args) {
    if (args[0] && typeof args[0] === 'string' && args[0].includes('Closing session:')) {
        return;
    }
    originalLog.apply(console, args);
};

process.on('uncaughtException', (err) => {
    console.error('🚨 [CRITICAL ERROR] Uncaught Exception. Engine is still running:', err.message);
});
process.on('unhandledRejection', (reason) => {
    console.error('🚨 [CRITICAL ERROR] Unhandled Promise Rejection:', reason);
});

const app = express();
app.use(express.json({ limit: '50mb' }));
const PORT = 3000;

let sock = null;
let reconnectTimer = null;
let isConnecting = false;
let unreadAlerts = [];
const MAX_UNREAD_ALERTS = 200;
let sseClients = [];
let lastQrDataUrl = null;
let lastStatus = null;
let browserOpenedOnce = false;
const SCRIPT_START_TIME = Math.floor(Date.now() / 1000);
const THIRTY_DAYS_SECONDS = 30 * 24 * 60 * 60;

const sessionDir = path.join(__dirname, '..', '..', '..', '..', 'Data', 'SessionCookies');
const mediaVaultDir = path.join(os.homedir(), 'Documents', 'Cortex', 'MediaVault', 'WhatsApp_Media');

try {
    if (!fs.existsSync(sessionDir)) {
        fs.mkdirSync(sessionDir, { recursive: true });
    }
    if (!fs.existsSync(mediaVaultDir)) {
        fs.mkdirSync(mediaVaultDir, { recursive: true });
    }
} catch (fsErr) {
    console.error("🚨 [FS ERROR] Cannot create session/media directory:", fsErr.message);
}

const dbPath = path.join(sessionDir, 'chats.db');
const db = new sqlite3.Database(dbPath, (err) => {
    if (err) console.error("🚨 [DB INIT ERROR] Failed to connect to SQLite:", err.message);
});

let dbReadyResolve;
const dbReadyPromise = new Promise((resolve) => { dbReadyResolve = resolve; });

db.serialize(() => {
    db.run(`CREATE TABLE IF NOT EXISTS Messages (
        id TEXT PRIMARY KEY,
        phone_number TEXT,
        text TEXT,
        timestamp INTEGER,
        from_me INTEGER
    )`, (err) => {
        if (err) console.error("🚨 [DB TABLE ERROR] Failed to create table:", err.message);
        else console.log("✅ SQLite Messages table is ready.");
    });

    db.run("CREATE INDEX IF NOT EXISTS idx_phone_timestamp ON Messages(phone_number, timestamp)", (err) => {
        if (err) console.error("⚠️ Index creation warning:", err.message);
    });

    db.all("PRAGMA table_info(Messages)", (err, columns) => {
        if (err) {
            dbReadyResolve();
            return;
        }
        const hasCreated = Array.isArray(columns) && columns.some((c) => c.name === 'created_at');
        if (!hasCreated) {
            db.run("ALTER TABLE Messages ADD COLUMN created_at INTEGER DEFAULT 0", (alterErr) => {
                if (alterErr) {
                    console.error("⚠️ [DB MIGRATION ERROR]:", alterErr.message);
                    dbReadyResolve();
                    return;
                }
                const now = Math.floor(Date.now() / 1000);
                db.run("UPDATE Messages SET created_at = ? WHERE created_at = 0 OR created_at IS NULL", [now], (updErr) => {
                    if (updErr) console.error("⚠️ [DB MIGRATION UPDATE ERROR]:", updErr.message);
                    else console.log("✅ Messages.created_at column added and backfilled.");
                    dbReadyResolve();
                });
            });
        } else {
            dbReadyResolve();
        }
    });
});

function dbRunAsync(sql, params) {
    return new Promise((resolve) => {
        db.run(sql, params, function (err) {
            if (err) console.error("⚠️ [DB INSERT ERROR]:", err.message);
            resolve();
        });
    });
}

function cleanupOldMessages() {
    const cutoff = Math.floor(Date.now() / 1000) - THIRTY_DAYS_SECONDS;
    db.run("DELETE FROM Messages WHERE created_at > 0 AND created_at < ?", [cutoff], function (err) {
        if (err) {
            console.error("❌ [DB CLEANUP ERROR]:", err.message);
        } else if (this.changes > 0) {
            console.log(`🧹 Cleaned up ${this.changes} messages added more than 30 days ago.`);
        }
    });
}

function cleanupMediaVault() {
    const cutoffMs = Date.now() - THIRTY_DAYS_SECONDS * 1000;
    try {
        const files = fs.readdirSync(mediaVaultDir);
        let deleted = 0;
        for (const file of files) {
            try {
                const fp = path.join(mediaVaultDir, file);
                const stats = fs.statSync(fp);
                if (stats.isFile() && stats.mtimeMs < cutoffMs) {
                    fs.unlinkSync(fp);
                    deleted++;
                }
            } catch (e) {}
        }
        if (deleted > 0) console.log(`🧹 Cleaned up ${deleted} old media file(s).`);
    } catch (e) {}
}

dbReadyPromise.then(() => {
    cleanupOldMessages();
    cleanupMediaVault();
    setInterval(cleanupOldMessages, 6 * 60 * 60 * 1000);
    setInterval(cleanupMediaVault, 6 * 60 * 60 * 1000);
});

function broadcast(payload) {
    lastStatus = payload;
    if (payload.type === 'waiting_scan' && payload.qr) {
        lastQrDataUrl = payload.qr;
    }
    const data = `data: ${JSON.stringify(payload)}\n\n`;
    const deadClients = [];
    sseClients.forEach((client) => {
        try {
            client.res.write(data);
        } catch (e) {
            deadClients.push(client);
        }
    });
    if (deadClients.length > 0) {
        sseClients = sseClients.filter((c) => !deadClients.includes(c));
    }
}

function openBrowser(url) {
    const platform = process.platform;
    let cmd;
    if (platform === 'win32') cmd = `start "" "${url}"`;
    else if (platform === 'darwin') cmd = `open "${url}"`;
    else cmd = `xdg-open "${url}"`;
    exec(cmd, () => {});
}

function normalizeJid(msgKey) {
    if (!msgKey) return null;
    const alt = msgKey.remoteJidAlt;
    const main = msgKey.remoteJid;
    let chosen = null;
    if (alt && typeof alt === 'string' && alt.endsWith('@s.whatsapp.net')) {
        chosen = alt;
    } else if (main && typeof main === 'string' && main.endsWith('@s.whatsapp.net')) {
        chosen = main;
    } else if (alt && typeof alt === 'string') {
        chosen = alt;
    } else if (main && typeof main === 'string') {
        chosen = main;
    }
    if (!chosen) return null;
    return chosen.replace(/:\d+@/, '@');
}

function extractMessageText(messageObj) {
    if (!messageObj) return "[Media Message]";
    const inner = messageObj.ephemeralMessage?.message ||
                  messageObj.viewOnceMessage?.message ||
                  messageObj.viewOnceMessageV2?.message ||
                  messageObj.documentWithCaptionMessage?.message ||
                  messageObj;

    return inner.conversation ||
           inner.extendedTextMessage?.text ||
           inner.imageMessage?.caption ||
           inner.videoMessage?.caption ||
           inner.documentMessage?.caption ||
           "[Media Message]";
}

function extractMediaContainer(messageObj) {
    if (!messageObj) return null;
    const inner = messageObj.ephemeralMessage?.message ||
                  messageObj.viewOnceMessage?.message ||
                  messageObj.viewOnceMessageV2?.message ||
                  messageObj.documentWithCaptionMessage?.message ||
                  messageObj;

    if (inner.imageMessage || inner.videoMessage || inner.documentMessage || inner.audioMessage) {
        return inner;
    }
    return null;
}

const store = {
    bind: (ev) => {
        const processMessages = async (messages, isBulkSync = false) => {
            try {
                if (!messages || !Array.isArray(messages) || messages.length === 0) return;

                let validMessageCount = 0;
                let uniqueChats = new Set();

                for (const msg of messages) {
                    try {
                        if (!msg || !msg.key || !msg.key.remoteJid || !msg.message) continue;

                        const jid = normalizeJid(msg.key);
                        if (!jid) continue;

                        if (
                            jid.endsWith('@g.us') ||
                            jid === 'status@broadcast' ||
                            jid.endsWith('@newsletter') ||
                            jid.endsWith('@broadcast')
                        ) {
                            continue;
                        }

                        const id = msg.key.id;
                        if (!id) continue;

                        const rawTs = typeof msg.messageTimestamp === 'object' && msg.messageTimestamp !== null
                            ? Number(msg.messageTimestamp.low || msg.messageTimestamp)
                            : Number(msg.messageTimestamp);
                        const timestamp = rawTs || Math.floor(Date.now() / 1000);
                        const fromMe = msg.key.fromMe ? 1 : 0;

                        const text = extractMessageText(msg.message);
                        const mediaContainer = extractMediaContainer(msg.message);
                        let mediaTag = "";

                        const recentMediaCutoff = SCRIPT_START_TIME - 86400;
                        const shouldDownloadMedia = mediaContainer && !fromMe && (!isBulkSync || timestamp >= recentMediaCutoff);
                        if (shouldDownloadMedia) {
                            try {
                                const buffer = await downloadMediaMessage(
                                    msg,
                                    'buffer',
                                    {},
                                    { logger: pino({ level: 'silent' }), reuploadRequest: sock?.updateMediaMessage }
                                );
                                let ext = 'bin';
                                if (mediaContainer.imageMessage) ext = 'jpg';
                                else if (mediaContainer.videoMessage) ext = 'mp4';
                                else if (mediaContainer.audioMessage) ext = 'mp3';
                                else if (mediaContainer.documentMessage) {
                                    const fname = mediaContainer.documentMessage.fileName || 'doc.pdf';
                                    const parsedExt = path.extname(fname).replace('.', '').toLowerCase();
                                    ext = parsedExt || 'pdf';
                                }
                                const safeId = String(id).replace(/[^a-zA-Z0-9_-]/g, '').slice(-10);
                                const filename = `${Date.now()}_${safeId}_wa.${ext}`;
                                const savePath = path.join(mediaVaultDir, filename).replace(/\\/g, '/');
                                await fs.promises.writeFile(savePath, buffer);
                                mediaTag = `\n[Media Attachment Saved]: ${savePath}`;
                                console.log(`📎 [WA MEDIA SAVED]: ${savePath}`);
                            } catch (dlErr) {
                                console.error("⚠️ Failed to download WhatsApp media:", dlErr.message);
                            }
                        }

                        const fullTextForDb = mediaTag ? `${text}${mediaTag}` : text;

                        if (!fromMe && !isBulkSync && timestamp >= SCRIPT_START_TIME) {
                            const senderName = msg.pushName || `Unknown ${jid.split('@')[0]}`;
                            unreadAlerts.push(`${senderName}: ${fullTextForDb}`);
                            if (unreadAlerts.length > MAX_UNREAD_ALERTS) {
                                unreadAlerts = unreadAlerts.slice(-MAX_UNREAD_ALERTS);
                            }
                        }

                        validMessageCount++;
                        uniqueChats.add(jid.split('@')[0]);

                        const nowSec = Math.floor(Date.now() / 1000);
                        const insertQuery = `INSERT OR IGNORE INTO Messages (id, phone_number, text, timestamp, from_me, created_at) VALUES (?, ?, ?, ?, ?, ?)`;
                        await dbRunAsync(insertQuery, [id, jid, fullTextForDb, timestamp, fromMe, nowSec]);
                    } catch (msgErr) {}
                }

                if (validMessageCount > 0) {
                    if (isBulkSync) {
                        console.log(`🔄 [OFFLINE/INITIAL SYNC] Loaded ${validMessageCount} personal messages across ${uniqueChats.size} chats (Groups Ignored).`);
                    } else if (validMessageCount === 1) {
                        console.log(`💬 [LIVE MESSAGE] Received a new personal message from ${Array.from(uniqueChats)[0]}`);
                    } else {
                        console.log(`⚡ [QUICK SYNC] Processed ${validMessageCount} recent personal messages from ${uniqueChats.size} chats.`);
                    }
                }
            } catch (mainErr) {}
        };

        ev.on('messages.upsert', ({ messages }) => {
            processMessages(messages, false);
        });

        ev.on('messaging-history.set', async ({ messages, progress, isLatest }) => {
            console.log(`\n⏳ [SYSTEM WAKING UP] Fetching pending history from WhatsApp servers...`);
            broadcast({ type: 'syncing_history', progress: typeof progress === 'number' ? progress : 0 });
            await processMessages(messages, true);
            if (isLatest) {
                broadcast({ type: 'history_loaded', count: Array.isArray(messages) ? messages.length : 0 });
                setTimeout(() => broadcast({ type: 'ready' }), 600);
            }
        });
    }
};

function scheduleReconnect(delayMs) {
    if (reconnectTimer) {
        clearTimeout(reconnectTimer);
        reconnectTimer = null;
    }
    reconnectTimer = setTimeout(() => {
        reconnectTimer = null;
        connectToWhatsApp();
    }, delayMs);
}

async function connectToWhatsApp() {
    if (isConnecting) return;
    isConnecting = true;

    if (reconnectTimer) {
        clearTimeout(reconnectTimer);
        reconnectTimer = null;
    }

    try {
        if (sock) {
            try {
                sock.ev.removeAllListeners();
                sock.ws?.close();
            } catch (e) {}
            sock = null;
        }

        const authPath = path.join(sessionDir, 'auth_info_baileys');
        const { state, saveCreds } = await useMultiFileAuthState(authPath);

        let version = undefined;
        try {
            const latest = await fetchLatestBaileysVersion();
            if (latest && latest.version) {
                version = latest.version;
            }
        } catch (verErr) {}

        const socketConfig = {
            auth: state,
            logger: pino({ level: 'silent' }),
            printQRInTerminal: false,
            browser: Browsers.windows('Desktop'),
            syncFullHistory: false
        };

        if (version) {
            socketConfig.version = version;
        }

        sock = makeWASocket(socketConfig);
        isConnecting = false;

        store.bind(sock.ev);

        sock.ev.on('connection.update', (update) => {
            try {
                const { connection, lastDisconnect, qr } = update;

                if (qr) {
                    qrcode.toDataURL(qr, {
                        margin: 1,
                        width: 340,
                        color: { dark: '#0a0a0f', light: '#ffffff' }
                    })
                        .then((dataUrl) => {
                            broadcast({ type: 'waiting_scan', qr: dataUrl });
                            if (!browserOpenedOnce) {
                                browserOpenedOnce = true;
                                setTimeout(() => openBrowser(`http://localhost:${PORT}/qr`), 300);
                            }
                        })
                        .catch(() => {});
                }

                if (connection === 'connecting') {
                    broadcast({ type: 'scanned' });
                }

                if (connection === 'close') {
                    const statusCode = lastDisconnect?.error?.output?.statusCode;
                    const shouldReconnect = statusCode !== DisconnectReason.loggedOut;

                    if (statusCode === DisconnectReason.loggedOut) {
                        console.log('❌ [LOGGED OUT] Device has been logged out from WhatsApp Web. Delete the session folder and restart to rescan.');
                        broadcast({ type: 'logged_out' });
                        try {
                            if (sock) {
                                sock.ev.removeAllListeners();
                                sock.ws?.close();
                            }
                        } catch (cleanupErr) {}
                        sock = null;
                        isConnecting = false;
                        return;
                    } else if (statusCode === 440) {
                        console.log('⚠️ [CONFLICT - 440] WhatsApp Web is open elsewhere. Jarvis is waiting 10s to avoid spam...');
                        broadcast({ type: 'disconnected', reason: 'Session conflict — WhatsApp Web open elsewhere. Reconnecting in 10s...' });
                        scheduleReconnect(10000);
                        return;
                    } else if (statusCode === DisconnectReason.timedOut) {
                        console.log('⚠️ [TIMEOUT] Connection is slow, attempting to reconnect in 5s...');
                        broadcast({ type: 'disconnected', reason: 'Connection timed out. Reconnecting in 5s...' });
                        scheduleReconnect(5000);
                        return;
                    } else {
                        console.log(`⚠️ [DISCONNECTED] Reason Code: ${statusCode || 'Unknown'}. Reconnecting...`);
                        broadcast({ type: 'disconnected', reason: `Disconnected (code ${statusCode || 'unknown'}). Reconnecting...` });
                    }

                    if (shouldReconnect) {
                        scheduleReconnect(3000);
                    }
                } else if (connection === 'open') {
                    lastQrDataUrl = null;
                    browserOpenedOnce = false;
                    console.log('\n✅ JARVIS WHATSAPP ENGINE IS ONLINE (MODULAR & SQLITE MODE)!\n');
                    broadcast({ type: 'connected' });
                }
            } catch (connErr) {
                console.error("🚨 [CONNECTION EVENT ERROR]:", connErr.message);
            }
        });

        sock.ev.on('creds.update', saveCreds);

    } catch (err) {
        isConnecting = false;
        console.error('❌ [ENGINE START ERROR] Failed to connect to WhatsApp:', err.message);
        scheduleReconnect(10000);
    }
}

app.get('/qr', (req, res) => {
    res.sendFile(path.join(__dirname, 'qr_page.html'));
});

app.get('/qr-events', (req, res) => {
    res.setHeader('Content-Type', 'text/event-stream');
    res.setHeader('Cache-Control', 'no-cache');
    res.setHeader('Connection', 'keep-alive');
    res.setHeader('X-Accel-Buffering', 'no');
    res.flushHeaders();

    const client = { res };
    sseClients.push(client);

    if (lastQrDataUrl && (!lastStatus || lastStatus.type === 'waiting_scan')) {
        try {
            res.write(`data: ${JSON.stringify({ type: 'waiting_scan', qr: lastQrDataUrl })}\n\n`);
        } catch (e) {}
    } else if (lastStatus) {
        try {
            res.write(`data: ${JSON.stringify(lastStatus)}\n\n`);
        } catch (e) {}
    }

    const keepAlive = setInterval(() => {
        try {
            res.write(`: ping\n\n`);
        } catch (e) {}
    }, 25000);

    req.on('close', () => {
        clearInterval(keepAlive);
        sseClients = sseClients.filter((c) => c !== client);
    });
});

app.post('/send', (req, res) => {
    try {
        sendMessageController(req, res, () => sock);
    } catch (err) {
        if (!res.headersSent) res.status(500).json({ error: "Internal Server Error in /send route" });
    }
});

app.post('/fetch-chats', (req, res) => {
    try {
        fetchChatsController(req, res, db);
    } catch (err) {
        if (!res.headersSent) res.status(500).json({ error: "Internal Server Error in /fetch-chats route" });
    }
});

app.get('/get-alerts', (req, res) => {
    if (unreadAlerts.length > 0) {
        const alertsToSend = [...unreadAlerts];
        unreadAlerts = [];
        res.json({ success: true, alerts: alertsToSend });
    } else {
        res.json({ success: true, alerts: [] });
    }
});

app.use((req, res) => {
    res.status(404).json({ error: "Route not found" });
});

app.listen(PORT, () => {
    console.log(`🔥 Local Bridge listening at http://localhost:${PORT}`);
    connectToWhatsApp();
});
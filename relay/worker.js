// Track Tidy telemetry relay (Cloudflare Worker).
//
// The app used to POST straight to a Discord webhook whose URL shipped,
// in plain text, inside every installer (default_credentials.json). On
// 2026-11-06 someone pulled it out of a public release, spammed the log
// channel with @everyone + invite links, then deleted the webhook. A
// webhook URL can't be protected once it's in a client, so the app now
// talks to this relay instead and the real webhook URL only ever exists
// here, as a Worker secret (DISCORD_WEBHOOK_URL).
//
// Anyone can still find this relay's URL in an installer. What they can
// do with it is deliberately narrow: post an embed that looks like one of
// the app's own telemetry events (known title, known field names, plain
// text values, no mentions, no links, no custom username/avatar, only the
// app's own attachment names). They can't post free text, ping anyone,
// or delete/rename the webhook.
//
// It also keeps the "Unique users" count the app used to compute itself
// by reading the channel's history with a Discord bot token (also shipped
// in every installer, also exposed). Here it's a set of keyed hashes
// (HMAC-SHA-256 with the USER_HASH_KEY Worker secret) of each "User" field
// value, stored in Workers KV (binding USERS), plus a running counter -
// the count is added to "New install" embeds on the way through. No bot
// token anywhere anymore.
//
// Keyed, not a plain SHA-256: Windows usernames are mostly first names, so
// a plain hash is reversed by hashing a list of common names. Without
// USER_HASH_KEY a stored key says nothing. The first 56 users were seeded
// as plain SHA-256 ("u:<sha>") before this existed; each one is moved to
// its keyed form ("h:<hmac>") the next time that user sends anything, and
// `python relay/deploy.py --purge-legacy` drops whatever is left.

const ALLOWED_TITLES = new Set([
  "Track reported",
  "Several tracks with no cover match",
  "New install",
  "App updated",
  "App uninstalled",
  "Scan complete",
  "Scan cancelled",
  "Rate limit reached",
  "Extraction complete",
  "Extraction cancelled",
  "Extraction failed",
  "Quality scan complete",
  "Quality scan cancelled",
  "Quality scan failed",
  "Unhandled exception",
]);

// Every field name any send_* function in track_tidy.py (or installer.iss's
// uninstall ping) sends. An unknown field is dropped rather than rejecting
// the whole event, so a newer app adding a field still gets its event
// through before this list is updated.
const ALLOWED_FIELD_NAMES = new Set([
  "User", "Reported by", "OS", "App version", "Previous version",
  "File", "Current title", "Current artist", "Suggested title", "Suggested artist",
  "Online cover match", "Format", "No cover match",
  "New files", "Removed files", "Total files",
  "iTunes matches", "SoundCloud matches", "AcoustID fallback used",
  "Rate-limited sources", "Auth errors", "Source",
  "Files moved", "Empty folders removed", "Error",
  "Green", "Orange", "Red", "Context",
]);

// Attachment name -> accepted kind. Only what the app itself sends.
const ALLOWED_ATTACHMENTS = {
  "current_cover.jpg": "image",
  "suggested_cover.jpg": "image",
  "no_cover_tracks.txt": "text",
  "traceback.txt": "text",
};
const MAX_IMAGE_BYTES = 4 * 1024 * 1024;
const MAX_TEXT_BYTES = 512 * 1024;
const MAX_BODY_BYTES = 9 * 1024 * 1024;
const MAX_FIELDS = 25;
const MAX_FIELD_VALUE = 1024;

// Same exclusions the app's old count_unique_discord_users() had: CI's own
// username and a one-off manual test string, not real people.
const UNIQUE_USER_EXCLUDED = new Set(["", "(unknown)", "runner", "test-install-verification"]);

// The relay URL is public, so anyone can post a well-formed event with a
// made-up "User". These caps keep that from inflating the count much (or
// burning the KV free tier's 1000 writes/day, which would silently stop
// the count for real installs): a new user is only counted if this IP
// hasn't already added NEW_USERS_PER_IP_PER_DAY today and the relay as a
// whole hasn't added NEW_USERS_PER_DAY. The event itself is still
// forwarded either way.
const NEW_USERS_PER_IP_PER_DAY = 3;
const NEW_USERS_PER_DAY = 100;
const DAY_SECONDS = 86400;
const COUNT_KEY = "meta:count";

// Per-IP limit. Uses the RATE_LIMITER binding (Cloudflare's Rate Limiting
// API, shared across isolates) when deploy.py set it up; otherwise falls
// back to this isolate's own memory, which only slows down a flood from
// one address.
const RATE_LIMIT_PER_MINUTE = 20;
const recentRequests = new Map();

async function isRateLimited(env, ip) {
  if (env.RATE_LIMITER) {
    try {
      const { success } = await env.RATE_LIMITER.limit({ key: ip });
      return !success;
    } catch (error) {
      // Fall through to the in-memory limit.
    }
  }
  return rateLimitedInMemory(ip);
}

function rateLimitedInMemory(ip) {
  const now = Date.now();
  const windowStart = now - 60_000;
  const timestamps = (recentRequests.get(ip) || []).filter((t) => t > windowStart);
  timestamps.push(now);
  recentRequests.set(ip, timestamps);
  if (recentRequests.size > 5000) {
    for (const [key, value] of recentRequests) {
      if (!value.length || value[value.length - 1] < windowStart) recentRequests.delete(key);
    }
  }
  return timestamps.length > RATE_LIMIT_PER_MINUTE;
}

// Plain-text only: no pings, no clickable links, no masked-link markdown.
// Embeds never ping anyway, and allowed_mentions is forced empty below;
// this is about what a fake event can make Kevin click on.
function neutralize(text) {
  return String(text)
    .replace(/@/g, "@​")
    .replace(/:\/\//g, ":​//")
    .replace(/discord\.(gg|com\/invite)/gi, (m) => m.replace(".", "​."))
    .replace(/\]\(/g, "]​(");
}

function badRequest(reason) {
  return new Response(JSON.stringify({ error: reason }), {
    status: 400,
    headers: { "content-type": "application/json" },
  });
}

function sanitizeEmbed(raw) {
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) return null;
  if (!ALLOWED_TITLES.has(raw.title)) return null;

  const embed = { title: raw.title };
  if (Number.isInteger(raw.color) && raw.color >= 0 && raw.color <= 0xffffff) {
    embed.color = raw.color;
  }

  const fields = [];
  for (const field of Array.isArray(raw.fields) ? raw.fields : []) {
    if (!field || typeof field !== "object") continue;
    if (!ALLOWED_FIELD_NAMES.has(field.name)) continue;
    let value = typeof field.value === "string" ? field.value : String(field.value ?? "");
    value = neutralize(value).slice(0, MAX_FIELD_VALUE) || "(empty)";
    fields.push({ name: field.name, value, inline: field.inline === true });
    if (fields.length >= MAX_FIELDS) break;
  }
  embed.fields = fields;

  for (const slot of ["thumbnail", "image"]) {
    const url = raw[slot] && raw[slot].url;
    if (typeof url === "string" && url.startsWith("attachment://")) {
      const name = url.slice("attachment://".length);
      if (ALLOWED_ATTACHMENTS[name] === "image") embed[slot] = { url };
    }
  }
  return embed;
}

// Returns the image's real MIME type, or null if it isn't JPEG/PNG/WebP.
// (Embedded covers are sometimes PNG even though the app names every
// attachment .jpg - Discord gets the real type.)
function imageType(bytes) {
  const b = new Uint8Array(bytes.slice(0, 12));
  if (b[0] === 0xff && b[1] === 0xd8 && b[2] === 0xff) return "image/jpeg";
  if (b[0] === 0x89 && b[1] === 0x50 && b[2] === 0x4e && b[3] === 0x47) return "image/png";
  if (b[0] === 0x52 && b[1] === 0x49 && b[2] === 0x46 && b[3] === 0x46 &&
      b[8] === 0x57 && b[9] === 0x45 && b[10] === 0x42 && b[11] === 0x50) return "image/webp";
  return null;
}

function toHex(buffer) {
  return [...new Uint8Array(buffer)].map((b) => b.toString(16).padStart(2, "0")).join("");
}

async function sha256Hex(text) {
  return toHex(await crypto.subtle.digest("SHA-256", new TextEncoder().encode(text)));
}

async function hmacHex(secret, text) {
  const key = await crypto.subtle.importKey(
    "raw", new TextEncoder().encode(secret), { name: "HMAC", hash: "SHA-256" }, false, ["sign"],
  );
  return toHex(await crypto.subtle.sign("HMAC", key, new TextEncoder().encode(text)));
}

async function readCount(env, key) {
  const value = Number(await env.USERS.get(key));
  return Number.isFinite(value) ? value : 0;
}

// Total distinct users. Kept as one counter key (one read) instead of
// listing every user key on each install; rebuilt from a full listing only
// if the counter is missing. KV has no atomic increment, so two brand-new
// users in the same instant can undercount by one - fine for a ballpark
// figure in a log channel.
async function totalUsers(env) {
  const stored = await env.USERS.get(COUNT_KEY);
  if (stored !== null && Number.isFinite(Number(stored))) return Number(stored);
  let total = 0;
  for (const prefix of ["h:", "u:"]) {
    let cursor;
    do {
      const page = await env.USERS.list({ prefix, cursor });
      total += page.keys.length;
      cursor = page.list_complete ? undefined : page.cursor;
    } while (cursor);
  }
  await env.USERS.put(COUNT_KEY, String(total));
  return total;
}

// Records this event's user (if any) and, for a "New install", returns
// the total distinct-user count to show. Never throws: a KV problem just
// means no count on this one event. Without USER_HASH_KEY nothing is
// stored at all (never falls back to an unkeyed hash).
async function trackUniqueUser(env, embed, rawUser, ip) {
  if (!env.USERS || !env.USER_HASH_KEY) return null;
  try {
    const normalized = (rawUser || "").trim().toLowerCase();
    if (!UNIQUE_USER_EXCLUDED.has(normalized)) {
      const key = "h:" + (await hmacHex(env.USER_HASH_KEY, "user:" + normalized));
      if ((await env.USERS.get(key)) === null) {
        const legacyKey = "u:" + (await sha256Hex(normalized));
        if ((await env.USERS.get(legacyKey)) !== null) {
          // Already counted under the old unkeyed hash: move it over.
          await env.USERS.put(key, "1");
          await env.USERS.delete(legacyKey);
        } else {
          await registerNewUser(env, key, ip);
        }
      }
    }
    if (embed.title !== "New install") return null;
    return await totalUsers(env);
  } catch (error) {
    return null;
  }
}

// Counts a never-seen user, unless this IP or the whole relay already hit
// today's cap (see NEW_USERS_PER_IP_PER_DAY). Returns whether it counted.
async function registerNewUser(env, key, ip) {
  const today = new Date().toISOString().slice(0, 10);
  const ipKey = `ip:${today}:` + (await hmacHex(env.USER_HASH_KEY, "ip:" + ip));
  const dayKey = `day:${today}`;
  const [fromIp, fromDay] = await Promise.all([readCount(env, ipKey), readCount(env, dayKey)]);
  if (fromIp >= NEW_USERS_PER_IP_PER_DAY || fromDay >= NEW_USERS_PER_DAY) return false;

  const total = await totalUsers(env);
  await Promise.all([
    env.USERS.put(key, "1"),
    env.USERS.put(ipKey, String(fromIp + 1), { expirationTtl: DAY_SECONDS }),
    env.USERS.put(dayKey, String(fromDay + 1), { expirationTtl: 2 * DAY_SECONDS }),
    env.USERS.put(COUNT_KEY, String(total + 1)),
  ]);
  return true;
}

async function handleEvent(request, env, ip) {
  // The app (requests, WinHttp) always sends Content-Length. Without it the
  // size check could be skipped by a chunked upload, and formData() would
  // buffer whatever arrives.
  const lengthHeader = request.headers.get("content-length");
  if (lengthHeader === null) return new Response("length required", { status: 411 });
  const length = Number(lengthHeader);
  if (!Number.isFinite(length) || length > MAX_BODY_BYTES) return badRequest("too large");

  const contentType = request.headers.get("content-type") || "";
  let payload;
  const files = [];

  if (contentType.startsWith("multipart/form-data")) {
    let form;
    try {
      form = await request.formData();
    } catch (error) {
      return badRequest("bad form");
    }
    try {
      payload = JSON.parse(form.get("payload_json") || "");
    } catch (error) {
      return badRequest("bad payload_json");
    }
    for (const [key, value] of form.entries()) {
      if (key === "payload_json") continue;
      if (!/^files\[[01]\]$/.test(key)) return badRequest("unexpected form part");
      if (typeof value === "string") return badRequest("file part expected");
      const kind = ALLOWED_ATTACHMENTS[value.name];
      if (!kind) return badRequest("unexpected attachment");
      const bytes = await value.arrayBuffer();
      if (kind === "image") {
        const type = bytes.byteLength <= MAX_IMAGE_BYTES ? imageType(bytes) : null;
        if (!type) return badRequest("bad image");
        files.push({ key, name: value.name, bytes, type });
      } else {
        if (bytes.byteLength > MAX_TEXT_BYTES) return badRequest("text too large");
        let text;
        try {
          text = new TextDecoder("utf-8", { fatal: true }).decode(bytes);
        } catch (error) {
          return badRequest("text not utf-8");
        }
        // Same treatment as field values: Discord previews .txt attachments
        // inline, so a fake crash report shouldn't carry a live link.
        files.push({
          key, name: value.name, bytes: new TextEncoder().encode(neutralize(text)), type: "text/plain; charset=utf-8",
        });
      }
    }
  } else if (contentType.startsWith("application/json")) {
    try {
      payload = await request.json();
    } catch (error) {
      return badRequest("bad json");
    }
  } else {
    return badRequest("unsupported content type");
  }

  if (!payload || !Array.isArray(payload.embeds) || payload.embeds.length !== 1) {
    return badRequest("exactly one embed expected");
  }
  const rawEmbed = payload.embeds[0];
  const embed = sanitizeEmbed(rawEmbed);
  if (!embed) return badRequest("unknown event");

  // Hash the raw (un-neutralized) User value, so the same person always
  // maps to the same key.
  const rawUserField = (Array.isArray(rawEmbed.fields) ? rawEmbed.fields : [])
    .find((f) => f && f.name === "User");
  const rawUser = rawUserField ? String(rawUserField.value ?? "") : "";

  const uniqueUsers = await trackUniqueUser(env, embed, rawUser, ip);
  if (uniqueUsers !== null) {
    const versionIndex = embed.fields.findIndex((f) => f.name === "App version");
    const countField = { name: "Unique users", value: String(uniqueUsers), inline: true };
    if (versionIndex === -1) embed.fields.push(countField);
    else embed.fields.splice(versionIndex, 0, countField);
  }

  // Only ever these keys - no content, username, avatar_url, tts, etc.
  const outgoing = { embeds: [embed], allowed_mentions: { parse: [] } };

  let discordResponse;
  if (files.length) {
    const form = new FormData();
    form.append("payload_json", JSON.stringify(outgoing));
    for (const file of files) {
      form.append(file.key, new Blob([file.bytes], { type: file.type }), file.name);
    }
    discordResponse = await fetch(env.DISCORD_WEBHOOK_URL, { method: "POST", body: form });
  } else {
    discordResponse = await fetch(env.DISCORD_WEBHOOK_URL, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(outgoing),
    });
  }

  if (discordResponse.ok) return new Response(null, { status: 204 });
  return new Response(JSON.stringify({ error: "upstream", status: discordResponse.status }), {
    status: 502,
    headers: { "content-type": "application/json" },
  });
}

export default {
  async fetch(request, env) {
    const url = new URL(request.url);

    if (request.method === "GET" && url.pathname === "/") {
      return new Response("track-tidy relay ok", { status: 200 });
    }
    if (url.pathname !== "/event") return new Response("not found", { status: 404 });
    if (request.method !== "POST") return new Response("method not allowed", { status: 405 });
    if (!env.DISCORD_WEBHOOK_URL) return new Response("relay not configured", { status: 503 });

    const ip = request.headers.get("cf-connecting-ip") || "unknown";
    if (await isRateLimited(env, ip)) return new Response("slow down", { status: 429 });

    try {
      return await handleEvent(request, env, ip);
    } catch (error) {
      return new Response("relay error", { status: 500 });
    }
  },
};

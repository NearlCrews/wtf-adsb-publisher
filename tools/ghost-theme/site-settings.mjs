import fs from "node:fs";
import path from "node:path";
import themePackage from "../../themes/wtf-adsb/package.json" with { type: "json" };

// The site's settings from publisher.json, which the publisher passes to the
// tools it starts as environment variables: the origin and contact address
// (src/wtf_adsb_publisher/site_origin.py applies the same rules) and the name,
// home heading, description, support link, author, and About file
// (site_identity.py). Each is read and checked here, once.

// The public site's origin: site_origin, passed as SITE_ORIGIN. Production
// builds require it; elsewhere it defaults to EXAMPLE_ORIGIN.
export const EXAMPLE_ORIGIN = "https://example.com";
const LABEL = "[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?";
// The last label starts with a letter, so IP addresses such as https://192.0.2.1
// and https://0x7f000001 are refused.
const FORM = new RegExp(`^https://(?:${LABEL}\\.)*(?=[a-z])${LABEL}$`);
const FALLBACK_CONTACT = "https://github.com/NearlCrews/wtf-adsb-publisher";

// SITE_ORIGIN, checked; null when it is unset or empty.
export function configuredOrigin() {
  const value = process.env.SITE_ORIGIN;
  if (!value) return null;
  if (!FORM.test(value) || new URL(value).hostname.length > 253)
    throw new Error(
      "SITE_ORIGIN must be an https:// origin such as https://example.com, in lower case, with no port, path, or trailing slash",
    );
  return value;
}

// The origin a production build names; a clear error when it is not set.
export function requiredOrigin() {
  const value = configuredOrigin();
  if (!value)
    throw new Error(
      "Production builds need SITE_ORIGIN, the public site's origin: set site_origin in publisher.json",
    );
  return value;
}

// Canonical links, feeds, sitemaps, and structured data name this origin, even in previews.
export const ORIGIN = configuredOrigin() || EXAMPLE_ORIGIN;

// The public contact address: contact_email in publisher.json, which the
// publisher passes as SITE_CONTACT_EMAIL. Null when unset or empty; pages then
// name no address.
const ATOM = "[A-Za-z0-9_%+-]+";
const DOMAIN_LABEL = "[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?";
const CONTACT_FORM = new RegExp(
  `^${ATOM}(?:\\.${ATOM})*@(?:${DOMAIN_LABEL}\\.)+${DOMAIN_LABEL}$`,
);
export function contactEmail() {
  const value = process.env.SITE_CONTACT_EMAIL;
  if (!value) return null;
  if (
    !CONTACT_FORM.test(value) ||
    value.length > 254 ||
    value.split("@")[0].length > 64
  )
    throw new Error(
      "SITE_CONTACT_EMAIL must be a plain email address such as contact@example.com",
    );
  return value;
}

// A User-Agent naming the site's about page, or the project page when no origin is set.
export function userAgent(product = "WTF-ADSB/1.0") {
  const value = configuredOrigin();
  return `${product} (+${value ? `${value}/about/` : FALLBACK_CONTACT})`;
}

// The site's identity. Each value is one line of 1 to `limit` characters with
// no leading or trailing spaces.
// Control characters, including the C1 range and the Unicode line and
// paragraph separators, and the byte order mark, which trim() removes and
// Python's strip() keeps.
const control = (character) => {
  const code = character.codePointAt(0);
  return (
    code < 0x20 ||
    (code >= 0x7f && code <= 0x9f) ||
    code === 0x2028 ||
    code === 0x2029 ||
    code === 0xfeff
  );
};
function text(variable, setting, limit) {
  const value = process.env[variable];
  if (!value) return null;
  const characters = [...value];
  if (
    value !== value.trim() ||
    characters.some(control) ||
    characters.length > limit
  )
    throw new Error(
      `${variable} must be 1 to ${limit} characters on one line, with no leading or trailing spaces: set ${setting} in publisher.json`,
    );
  return value;
}

// The name used when site_name is unset, outside production.
export const DEFAULT_NAME = "ADS-B findings";
// The theme's own defaults for the optional settings, which a Ghost-rendered
// site shows until its owner changes them in Ghost Admin.
const custom = themePackage.config.custom;
export const DEFAULT_TAGLINE = custom.site_tagline.default;
export const DEFAULT_DESCRIPTION = custom.publication_description.default;
export const DEFAULT_AUTHOR = "the site";

// site_name, passed as SITE_NAME; null when unset or empty.
export function configuredName() {
  return text("SITE_NAME", "site_name", 60);
}

// The name a production build shows; a clear error when it is not set.
export function requiredName() {
  const value = configuredName();
  if (!value)
    throw new Error(
      "Production builds need SITE_NAME, the site's name: set site_name in publisher.json",
    );
  return value;
}

// Titles, the footer, the error pages, and the social images name the site.
export const SITE_NAME = configuredName() || DEFAULT_NAME;
// The home page heading: site_tagline, passed as SITE_TAGLINE.
export const SITE_TAGLINE =
  text("SITE_TAGLINE", "site_tagline", 120) || DEFAULT_TAGLINE;

// The line under the home heading, in the footer, and in the home page's meta
// description: site_description, passed as SITE_DESCRIPTION.
export const SITE_DESCRIPTION =
  text("SITE_DESCRIPTION", "site_description", 160) || DEFAULT_DESCRIPTION;

// The author page and story bylines name this author: author_name, passed as
// SITE_AUTHOR. It needs a letter or digit, since its slug is the page address.
export function authorName() {
  const value = text("SITE_AUTHOR", "author_name", 60);
  if (value === null) return DEFAULT_AUTHOR;
  if (!/[A-Za-z0-9]/.test(value))
    throw new Error(
      "SITE_AUTHOR must contain a letter or digit from A to Z or 0 to 9",
    );
  return value;
}

// The optional support or donation link: support_link, passed as
// SITE_SUPPORT_URL and SITE_SUPPORT_LABEL. Null when neither is set; pages then
// show no support link.
const SUPPORT_URL = new RegExp(
  `^https://(?:${LABEL}\\.)*(?=[a-z])${LABEL}(?:[/?#][A-Za-z0-9._~:/?#\\[\\]@!$&()*+,;=%-]*)?$`,
);
export function supportLink() {
  const url = process.env.SITE_SUPPORT_URL;
  const label = text("SITE_SUPPORT_LABEL", "support_link.label", 40);
  if (!url && !label) return null;
  if (!url || !label)
    throw new Error(
      "SITE_SUPPORT_URL and SITE_SUPPORT_LABEL must be set together: set both url and label in support_link",
    );
  if (!SUPPORT_URL.test(url) || url.length > 2000)
    throw new Error(
      "SITE_SUPPORT_URL must be an https:// address such as https://example.com/support, with a lower-case host name and no port",
    );
  return { url, label };
}

// The operator's About text: about_file, passed as SITE_ABOUT_FILE. Null when
// unset, empty, or /dev/null, the container template's stand-in; the About page
// then keeps its generic introduction. Otherwise the file's text, which must be
// 1 to 65536 bytes of UTF-8, and its modification time, which dates the page.
export const ABOUT_LIMIT = 64 * 1024;
export function aboutText() {
  const file = process.env.SITE_ABOUT_FILE;
  if (!file) return null;
  const fail = (problem) => {
    throw new Error(
      `SITE_ABOUT_FILE ${problem}: set about_file in publisher.json`,
    );
  };
  if (!path.isAbsolute(file) || file !== file.trim())
    fail("must be an absolute path");
  const info = fs.statSync(file);
  // A bind-mounted /dev/null is another inode of the same device.
  if (info.isCharacterDevice() && info.rdev === fs.statSync("/dev/null").rdev)
    return null;
  if (!info.isFile()) fail("must name a regular file");
  const data = fs.readFileSync(file);
  if (data.length > ABOUT_LIMIT) fail(`must be at most ${ABOUT_LIMIT} bytes`);
  let value;
  try {
    // The decoder drops a leading byte order mark.
    value = new TextDecoder("utf-8", { fatal: true }).decode(data);
  } catch {
    fail("must be UTF-8 text");
  }
  if (!value.trim()) fail("is empty");
  const modified = new Date(Math.floor(info.mtimeMs / 1000) * 1000)
    .toISOString()
    .replace(".000Z", "Z");
  return { text: value, modified };
}

// A small Markdown renderer for the operator's About text (about_file in
// publisher.json). It knows paragraphs, "#" to "######" headings (one or two
// marks make an h2 and more an h3, since the page title is the only h1), "-",
// "*", or "+" lists and numbered lists, [links](https://...), **bold**,
// *italic*, and `code`. A block that starts with "<" is HTML and passes
// through unchanged, so a plain HTML fragment works too. Other text is
// escaped. Links may be https://, http://, mailto:, or site-relative ("/" or
// "#"); any other link stays as its text.

const escapeHtml = (value) =>
  value
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");

const HEADING = /^(#{1,6})\s+(.+?)(?:\s+#+)?\s*$/;
const ITEM = /^\s*(?:([-*+])|\d{1,9}[.)])\s+(.*)$/;
const HTML_BLOCK = /^\s*</;

// One link, or null when its address is not allowed.
function link(label, url) {
  const decoded = url.replaceAll("&amp;", "&");
  if (/^(?:https?:\/\/|mailto:)/i.test(decoded))
    return `<a href="${url}" rel="external noopener noreferrer">${label}</a>`;
  if (/^[/#]/.test(decoded) && !decoded.startsWith("//"))
    return `<a href="${url}">${label}</a>`;
  return null;
}

const emphasis = (html) =>
  html
    .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
    .replace(/(^|[^*\w])\*([^*\s][^*]*)\*(?![*\w])/g, "$1<em>$2</em>");

// Inline text: code spans first, then escaped text with links and emphasis.
// Each link becomes a NUL-delimited placeholder while the emphasis passes run,
// so an asterisk in an address cannot turn into markup inside the link.
function inline(text) {
  return text
    .replaceAll("\0", "")
    .split(/(`[^`]+`)/)
    .map((part, index) => {
      if (index % 2) return `<code>${escapeHtml(part.slice(1, -1))}</code>`;
      const links = [];
      const swapped = escapeHtml(part).replace(
        /\[([^\]]+)\]\(([^()\s]+)\)/g,
        (match, label, url) => {
          const html = link(emphasis(label), url);
          if (html === null) return match;
          links.push(html);
          return `\0${links.length - 1}\0`;
        },
      );
      return emphasis(swapped).replace(/\0(\d+)\0/g, (_, n) => links[n]);
    })
    .join("");
}

const starts = (line) =>
  HTML_BLOCK.test(line) || HEADING.test(line) || ITEM.test(line);

// The HTML fragment for `source`, one block per line.
export function renderMarkdown(source) {
  const lines = source.replace(/^\uFEFF/, "").split(/\r\n?|\n/);
  const blocks = [];
  let index = 0;
  while (index < lines.length) {
    const line = lines[index];
    if (!line.trim()) {
      index += 1;
      continue;
    }
    if (HTML_BLOCK.test(line)) {
      const block = [];
      while (index < lines.length && lines[index].trim())
        block.push(lines[index++]);
      blocks.push(block.join("\n"));
      continue;
    }
    const heading = HEADING.exec(line);
    if (heading) {
      const level = heading[1].length <= 2 ? 2 : 3;
      blocks.push(`<h${level}>${inline(heading[2])}</h${level}>`);
      index += 1;
      continue;
    }
    let item = ITEM.exec(line);
    if (item) {
      const ordered = !item[1];
      const items = [];
      while (
        index < lines.length &&
        (item = ITEM.exec(lines[index])) &&
        !item[1] === ordered
      ) {
        let text = item[2];
        index += 1;
        // An indented line that starts no new item continues this one.
        while (
          index < lines.length &&
          /^\s+\S/.test(lines[index]) &&
          !ITEM.test(lines[index])
        )
          text += " " + lines[index++].trim();
        items.push(`<li>${inline(text)}</li>`);
      }
      const tag = ordered ? "ol" : "ul";
      blocks.push(`<${tag}>\n${items.join("\n")}\n</${tag}>`);
      continue;
    }
    const paragraph = [line.trim()];
    index += 1;
    while (index < lines.length && lines[index].trim() && !starts(lines[index]))
      paragraph.push(lines[index++].trim());
    blocks.push(`<p>${inline(paragraph.join(" "))}</p>`);
  }
  return blocks.join("\n") + "\n";
}

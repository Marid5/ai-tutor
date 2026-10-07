// Render the PNG app icons (public/icon-192.png, public/icon-512.png) from
// public/favicon.svg, so the installable-app manifest has raster icons that
// always match the vector one. Run after changing favicon.svg:
//
//   node scripts/render-icons.mjs        (from frontend/)
//
// PW_CHROMIUM_ARGS (optional, space-separated) passes extra Chromium flags, as
// for the browser tests.
import { readFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import { chromium } from '@playwright/test';

const publicDir = fileURLToPath(new URL('../public/', import.meta.url));
const svg = await readFile(`${publicDir}favicon.svg`, 'utf8');
const args = (process.env.PW_CHROMIUM_ARGS ?? '').split(/\s+/).filter(Boolean);

const browser = await chromium.launch({ args });
try {
  // One page, resized per icon: under --single-process closing a page's
  // context would take the whole browser down.
  const page = await browser.newPage({ deviceScaleFactor: 1 });
  for (const size of [192, 512]) {
    await page.setViewportSize({ width: size, height: size });
    // The SVG scales to fill the viewport; the corners outside its rounded
    // square stay transparent.
    await page.setContent(
      `<!doctype html><style>html,body{margin:0;background:transparent}svg{display:block;width:${size}px;height:${size}px}</style>${svg}`,
    );
    const path = `${publicDir}icon-${size}.png`;
    await page.screenshot({ path, omitBackground: true });
    console.log(`wrote ${path}`);
  }
} finally {
  await browser.close();
}

const { chromium } = require('playwright');

const TARGET_URL = 'https://www.pagasa.dost.gov.ph/radar';
const KEYWORDS = [
  'radar', 'mosaic', 'qpe', 'rain', 'reflectivity',
  'dbz', 'image', 'png', 'jpg', 'jpeg', 'tif', 'geojson', 'json', 'himawari'
];

function matchesKeyword(url) {
  const lower = url.toLowerCase();
  return KEYWORDS.some(k => lower.includes(k));
}

(async () => {
  const browser = await chromium.launch({ headless: true });
  const context = await browser.newContext();
  const page = await context.newPage();

  const seen = new Set();

  page.on('request', (request) => {
    const url = request.url();
    if (matchesKeyword(url) && !seen.has(url)) {
      seen.add(url);
      console.log('[REQUEST]', request.method(), url);
    }
  });

  page.on('response', async (response) => {
    const url = response.url();
    if (matchesKeyword(url)) {
      const ct = response.headers()['content-type'] || '';
      console.log('[RESPONSE]', response.status(), ct, url);
    }
  });

  console.log(`Navigating to ${TARGET_URL} ...`);
  await page.goto(TARGET_URL, { waitUntil: 'networkidle', timeout: 60000 });

  await page.waitForTimeout(10000);

  console.log(`\nDone. ${seen.size} matching requests captured.`);
  await browser.close();
})().catch((error) => {
  console.error('PROBE ERROR:', error);
  process.exit(1);
});

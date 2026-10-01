// CF Pages Function: detect country via CF-IPCountry and add X-Suggested-Market header.
function countryToMarket(c) {
  if (['HK', 'MO'].includes(c)) return 'HK';
  if (['CN', 'TW'].includes(c)) return 'HK';
  if (['JP'].includes(c)) return 'JP';
  if (['US', 'CA', 'GB', 'AU', 'NZ', 'SG', 'IN', 'PH', 'MY', 'TH', 'VN', 'ID', 'KR'].includes(c)) return 'US';
  return 'US';
}
export async function onRequestGet(context) {
  const request = context.request;
  const country = request.headers.get('CF-IPCountry') || 'XX';
  return new Response(JSON.stringify({
    country,
    suggested_market: countryToMarket(country),
  }), {
    status: 200,
    headers: { 'content-type': 'application/json' },
  });
}

const CACHE='napominalka-v2';
const FILES=['./','./index.html','./zapis-tekst.html','./zapis-audio.html','./zapis-foto.html','./robots.txt','./manifest.json','./manifest-tekst.json','./manifest-audio.json','./manifest-foto.json','./icons/icon-main-192.png','./icons/icon-main-512.png','./icons/icon-tekst-192.png','./icons/icon-tekst-512.png','./icons/icon-audio-192.png','./icons/icon-audio-512.png','./icons/icon-foto-192.png','./icons/icon-foto-512.png'];
self.addEventListener('install',e=>{e.waitUntil(caches.open(CACHE).then(c=>c.addAll(FILES)).then(()=>self.skipWaiting()))});
self.addEventListener('activate',e=>{e.waitUntil(caches.keys().then(ks=>Promise.all(ks.filter(k=>k!==CACHE).map(k=>caches.delete(k)))).then(()=>self.clients.claim()))});
self.addEventListener('fetch',e=>{
  const u=new URL(e.request.url);
  if(e.request.method!=='GET'||u.origin!==self.location.origin)return;
  e.respondWith(fetch(e.request).then(r=>{const c=r.clone();caches.open(CACHE).then(k=>k.put(e.request,c));return r})
    .catch(()=>caches.match(e.request).then(m=>m||caches.match('./index.html'))));
});

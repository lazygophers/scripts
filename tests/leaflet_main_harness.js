// 用最小的假 Leaflet / fetch / EventSource 跑 live_server 的 LEAFLET_MAIN，验证增量刷新
const fs=require('fs');const MAIN=fs.readFileSync(process.argv[2],'utf8');
let data={type:'FeatureCollection',features:[]};const events=[];let es;
const mkLayer=f=>({feature:f,bindTooltip(){return this},bindPopup(){return this}});
const L={map(){const h={};const m={on(n,f){(h[n]=h[n]||[]).push(f)},fire(n,e){events.push([n,e]);(h[n]||[]).forEach(f=>f(e))},fitBounds(){},setView(){}};return m;},
  tileLayer(){return {addTo(){return this}}},control:{layers(){return {addTo(){return this},addOverlay(){},addBaseLayer(){}}}},circleMarker(){return mkLayer()},
  geoJSON(_,o){const ls=new Set();return {addTo(){return this},addData(f){if(!f.geometry)return this;const l=mkLayer(f);ls.add(l);o.onEachFeature(f,l);return this},removeLayer(l){ls.delete(l)},getBounds(){},size:()=>ls.size,ls};}};
global.L=L;global.document={getElementById:()=>({})};global.console.warn=(...a)=>{console.log('WARN',...a)};
global.fetch=u=>Promise.resolve(u.startsWith('/data')?{ok:true,json:()=>Promise.resolve(JSON.parse(JSON.stringify(data)))}:{ok:false});
global.EventSource=function(){es=this;this.h={};this.addEventListener=(n,f)=>this.h[n]=f;};
const pt=(x,n)=>({type:'Feature',geometry:{type:'Point',coordinates:[x,0]},properties:{name:n}});
data.features=[pt(1,'a'),pt(2,'b'),pt(2,'b'),pt(3,'c'),{type:'Feature',geometry:null,properties:{}}];
new Function('STEM','FILE',MAIN)('地图','地图.geojson');
const tick=ms=>new Promise(r=>setTimeout(r,ms));
(async()=>{await tick(50);
  const load=events.find(e=>e[0]==='geojsonload');const layer=load[1].layer;console.log('初始',layer.size());
  data.features=[pt(1,'a'),pt(2,'b'),pt(4,'d'),{type:'Feature',geometry:null,properties:{}}];   // 删 c、删一个重复 b、加 d
  es.h.change({data:JSON.stringify({path:'地图.geojson'})});await tick(400);
  const up=events.filter(e=>e[0]==='geojsonupdate').pop();console.log('更新 加',up[1].added.length,'删',up[1].removed.length,'现有',layer.size());
  es.h.change({data:JSON.stringify({path:'地图.geojson'})});await tick(400);
  console.log('数据没变时 geojsonupdate 次数',events.filter(e=>e[0]==='geojsonupdate').length);
  es.h.change({data:JSON.stringify({path:'tiles/5/1/1.png'})});await tick(900);
  console.log('瓦片变化事件',events.some(e=>e[0]==='tilesupdate'));process.exit(0);})();

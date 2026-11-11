// Click a panel header to minimize; drag it to reposition without resizing the map.
(() => {
  const stacking=[];
  function bringToFront(panel){
    const index=stacking.indexOf(panel);
    if(index===stacking.length-1&&panel.style.zIndex)return;
    if(index>=0)stacking.splice(index,1);
    stacking.push(panel);
    stacking.forEach((item,i)=>{item.style.zIndex=String(40+i);});
  }
  function bind(panel){
    if(panel.dataset.draggable)return;
    panel.dataset.draggable='true';
    stacking.push(panel);
    panel.addEventListener('pointerenter',()=>bringToFront(panel));
    panel.addEventListener('pointerdown',()=>bringToFront(panel),true);
    panel.addEventListener('click',()=>bringToFront(panel),true);
    panel.addEventListener('focusin',()=>bringToFront(panel));
    const header=panel.querySelector(':scope > summary');if(!header)return;
    header.title='Click to minimize or expand. Drag to move.';
    let drag=null,suppress=false;
    header.addEventListener('pointerdown',event=>{
      if(event.button!==0)return;
      const rect=panel.getBoundingClientRect();
      drag={x:event.clientX,y:event.clientY,left:rect.left,top:rect.top,moved:false};
      header.setPointerCapture(event.pointerId);
    });
    header.addEventListener('pointermove',event=>{
      if(!drag)return;
      const dx=event.clientX-drag.x,dy=event.clientY-drag.y;
      if(!drag.moved&&Math.hypot(dx,dy)<6)return;
      drag.moved=true;panel.classList.add('dragging');bringToFront(panel);
      panel.style.right='auto';panel.style.bottom='auto';
      panel.style.left=Math.max(8,Math.min(innerWidth-panel.offsetWidth-8,drag.left+dx))+'px';
      panel.style.top=Math.max(8,Math.min(innerHeight-header.offsetHeight-8,drag.top+dy))+'px';
    });
    function finish(){if(drag){suppress=drag.moved;drag=null;panel.classList.remove('dragging');}}
    header.addEventListener('pointerup',finish);header.addEventListener('pointercancel',finish);
    header.addEventListener('click',event=>{if(suppress){event.preventDefault();suppress=false;}});
    function fit(){
      if(!panel.style.left)return;
      const rect=panel.getBoundingClientRect();
      panel.style.left=Math.max(8,Math.min(innerWidth-rect.width-8,rect.left))+'px';
      panel.style.top=Math.max(8,Math.min(innerHeight-rect.height-8,rect.top))+'px';
    }
    panel.addEventListener('toggle',fit);window.addEventListener('resize',fit);
  }
  document.querySelectorAll('.floating-panel').forEach(bind);
  new MutationObserver(()=>{
    for(let i=stacking.length-1;i>=0;i--)if(!stacking[i].isConnected)stacking.splice(i,1);
    document.querySelectorAll('.floating-panel').forEach(bind);
  }).observe(document.body,{childList:true,subtree:true});
})();

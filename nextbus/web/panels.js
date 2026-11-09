// Click a panel header to minimize; drag it to reposition without resizing the map.
(() => {
  let highest=40;
  function bind(panel){
    if(panel.dataset.draggable)return;
    panel.dataset.draggable='true';
    const header=panel.querySelector(':scope > summary');if(!header)return;
    header.title='Click to minimize or expand. Drag to move.';
    let drag=null,suppress=false;
    header.addEventListener('pointerdown',event=>{
      if(event.button!==0)return;
      const rect=panel.getBoundingClientRect();
      drag={x:event.clientX,y:event.clientY,left:rect.left,top:rect.top,width:rect.width,moved:false};
      header.setPointerCapture(event.pointerId);
    });
    header.addEventListener('pointermove',event=>{
      if(!drag)return;
      const dx=event.clientX-drag.x,dy=event.clientY-drag.y;
      if(!drag.moved&&Math.hypot(dx,dy)<6)return;
      drag.moved=true;panel.classList.add('dragging');panel.style.zIndex=String(++highest);
      panel.style.right='auto';panel.style.bottom='auto';panel.style.width=drag.width+'px';
      panel.style.left=Math.max(8,Math.min(innerWidth-drag.width-8,drag.left+dx))+'px';
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
})();

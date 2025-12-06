async function checkCam(e, id){
  if(e) e.preventDefault();
  try {
    const res = await fetch(`/cameras/${id}/check`, {method:'POST'});
    if(!res.ok){ return false; }
    const data = await res.json();
    const dot = document.getElementById('dot-'+id);
    const label = document.getElementById('status-'+id);
    if(dot){
      dot.classList.remove('dot-online','dot-offline','dot-unknown');
      if(data.online){ dot.classList.add('dot-online'); }
      else { dot.classList.add('dot-offline'); }
    }
    if(label){ label.textContent = data.online ? 'online' : 'offline'; }
  } catch (err) {
    console.error('checkCam failed', err);
  }
  return false;
}

// Dashboard camera modal logic
function setupCameraModal(){
  const modalEl = document.getElementById('cameraModal');
  if(!modalEl) return;
  const imgEl = document.getElementById('modalStreamImg');
  const nameEl = document.getElementById('modalCamName');
  const openLinkEl = document.getElementById('modalOpenLink');
  let bsModal = null;
  let pendingNavUrl = null;

  // lazy create BS modal on first use
  function ensureModal(){
    if(!bsModal && window.bootstrap){
      bsModal = new window.bootstrap.Modal(modalEl, {keyboard:true});
    }
    return bsModal;
  }

  // bind all open buttons
  document.querySelectorAll('.open-modal[data-cam-id]')
    .forEach(btn => {
      btn.addEventListener('click', () => {
        const camId = btn.getAttribute('data-cam-id');
        const camName = btn.getAttribute('data-cam-name') || '';
        if(nameEl) nameEl.textContent = camName;
        const streamUrl = `/cameras/${camId}/stream.mjpg`;
        const viewUrl = `/cameras/${camId}/view`;
        if(openLinkEl) openLinkEl.href = viewUrl;
        if(imgEl){
          // Set src right before showing to start the stream
          imgEl.src = streamUrl;
          imgEl.alt = `Live stream of ${camName}`;
        }
        const m = ensureModal();
        if(m) m.show();
      });
    });

  // When modal hides, clear the img src to stop the stream request
  modalEl.addEventListener('hidden.bs.modal', () => {
    if(imgEl){ imgEl.src = ''; }
    // If a navigation was requested from the arrow link, perform it now
    if(pendingNavUrl){
      const url = pendingNavUrl;
      pendingNavUrl = null;
      window.location.href = url;
    }
  });

  // Handle "Open full page view" arrow inside modal: close modal first, then navigate
  if(openLinkEl){
    openLinkEl.addEventListener('click', (e) => {
      e.preventDefault();
      const href = openLinkEl.getAttribute('href');
      if(!href || href === '#') return;
      pendingNavUrl = href;
      const m = ensureModal();
      if(m){
        m.hide();
      } else {
        // Fallback if Bootstrap modal isn't available
        const url = pendingNavUrl;
        pendingNavUrl = null;
        window.location.href = url;
      }
    });
  }
}

function showFlashedToasts(){
  if(!window.bootstrap) return;
  document.querySelectorAll('.toast').forEach(function(el){
    try { new bootstrap.Toast(el).show(); } catch(e) {}
  });
}

document.addEventListener('DOMContentLoaded', function(){
  setupCameraModal();
  showFlashedToasts();
});

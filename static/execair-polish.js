(function(){
  function qs(s){return document.querySelector(s)}
  function qsa(s){return Array.from(document.querySelectorAll(s))}
  function showToast(title,msg){
    var t=document.createElement('div');t.className='toast';
    t.innerHTML='<strong>'+title+'</strong><small>'+msg+'</small>';
    document.body.appendChild(t);setTimeout(function(){t.remove()},4200);
  }
  document.addEventListener('keydown',function(e){
    if((e.ctrlKey||e.metaKey)&&e.key.toLowerCase()==='k'){
      var input=qs('input[name="q"], input[type="search"], input[placeholder*="Search"], input[placeholder*="search"]');
      if(input){e.preventDefault();input.focus();showToast('Search ready','Use Ctrl+K any time to jump to search.');}
    }
  });
  qsa('form[data-confirm]').forEach(function(f){
    f.addEventListener('submit',function(e){if(!confirm(f.getAttribute('data-confirm'))){e.preventDefault();}})
  });
  var tables=qsa('.table-wrap table tbody');
  tables.forEach(function(tb){ if(!tb.children.length){ var tr=document.createElement('tr'); var td=document.createElement('td'); td.colSpan=20; td.innerHTML='<div class="empty-state"><strong>No records found</strong><span>Change filters or add a new record.</span></div>'; tr.appendChild(td); tb.appendChild(tr); } });
})();

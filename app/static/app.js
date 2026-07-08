(() => {
  function escapeHtml(value) {
    return String(value).replace(/[&<>'"]/g, ch => ({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[ch]));
  }

  function initUserPicker() {
    const nameInput = document.getElementById('scrum-master-name');
    const idInput = document.getElementById('scrum-master-id');
    const results = document.getElementById('user-results');
    if (!nameInput || !idInput || !results) return;

    let timer;
    nameInput.addEventListener('input', () => {
      clearTimeout(timer);
      const q = nameInput.value.trim();
      if (q.length < 2) {
        results.innerHTML = '';
        results.classList.remove('open');
        return;
      }
      timer = setTimeout(async () => {
        try {
          const response = await fetch(`/api/jira/users?q=${encodeURIComponent(q)}`);
          if (!response.ok) throw new Error('User search failed');
          const users = await response.json();
          results.innerHTML = '';
          users.forEach(user => {
            const button = document.createElement('button');
            button.type = 'button';
            button.innerHTML = `<strong>${escapeHtml(user.displayName || user.accountId)}</strong><span>${escapeHtml(user.accountId || '')}</span>`;
            button.addEventListener('click', () => {
              nameInput.value = user.displayName || '';
              idInput.value = user.accountId || '';
              results.innerHTML = '';
              results.classList.remove('open');
            });
            results.appendChild(button);
          });
          results.classList.toggle('open', users.length > 0);
        } catch (error) {
          results.innerHTML = '<div class="picker-error">Unable to search Jira users</div>';
          results.classList.add('open');
        }
      }, 250);
    });

    document.addEventListener('click', event => {
      if (!results.contains(event.target) && event.target !== nameInput) results.classList.remove('open');
    });
  }

  function initScanForm() {
    const scanForm = document.getElementById('scan-form');
    if (!scanForm) return;
    scanForm.addEventListener('submit', () => {
      const button = scanForm.querySelector('button[type="submit"]');
      if (button) {
        button.disabled = true;
        button.textContent = 'Scanning Jira hierarchy...';
      }
      let status = document.getElementById('scan-progress');
      if (!status) {
        status = document.createElement('div');
        status.id = 'scan-progress';
        status.className = 'scan-progress';
        status.textContent = 'Loading top-level tickets, Epics, Stories and selected compliance fields in bulk. Keep this page open.';
        scanForm.appendChild(status);
      }
    });
  }

  function initCustomCriteria() {
    const body = document.getElementById('custom-criteria-body');
    const template = document.getElementById('custom-criterion-template');
    const addButton = document.getElementById('add-criterion');
    const loadButton = document.getElementById('load-project-fields');
    const projectInput = document.getElementById('field-project');
    const status = document.getElementById('field-load-status');
    const warning = document.getElementById('project-field-warning');
    if (!body || !template) return;

    function bindRemove(row) {
      const button = row.querySelector('.remove-criterion');
      if (button) button.addEventListener('click', () => row.remove());
    }

    body.querySelectorAll('.custom-criterion-row').forEach(bindRemove);

    if (addButton) {
      addButton.addEventListener('click', () => {
        const fragment = template.content.cloneNode(true);
        const row = fragment.querySelector('.custom-criterion-row');
        body.appendChild(fragment);
        if (row) bindRemove(row);
      });
    }

    function replaceFieldOptions(fields) {
      document.querySelectorAll('.custom-field-select').forEach(select => {
        const selected = select.value || select.dataset.selected || '';
        const previousText = select.selectedOptions[0] ? select.selectedOptions[0].textContent : selected;
        select.innerHTML = '';
        const blank = document.createElement('option');
        blank.value = '';
        blank.textContent = '- Select Jira field -';
        select.appendChild(blank);
        fields.forEach(field => {
          const option = document.createElement('option');
          option.value = field.id;
          option.textContent = `${field.name} (${field.id})${field.required ? ' - required by Jira' : ''}`;
          if (field.id === selected) option.selected = true;
          select.appendChild(option);
        });
        if (selected && !fields.some(field => field.id === selected)) {
          const saved = document.createElement('option');
          saved.value = selected;
          saved.textContent = `${previousText || selected} - saved field not returned for this project`;
          saved.selected = true;
          select.appendChild(saved);
        }
      });
    }

    if (loadButton && projectInput) {
      loadButton.addEventListener('click', async () => {
        const project = projectInput.value.trim().split(',')[0].trim();
        if (!project) {
          status.textContent = 'Enter a Jira project key first.';
          return;
        }
        loadButton.disabled = true;
        loadButton.textContent = 'Loading Jira fields...';
        status.textContent = `Querying field metadata for ${project}...`;
        if (warning) warning.classList.add('hidden');
        try {
          const response = await fetch(`/api/jira/project-fields?project=${encodeURIComponent(project)}&refresh=true`);
          const payload = await response.json();
          if (!response.ok) throw new Error(payload.detail || 'Project field lookup failed');
          replaceFieldOptions(payload.fields || []);
          status.innerHTML = `<strong>${(payload.fields || []).length}</strong> fields loaded via ${escapeHtml(String(payload.source || 'Jira').replace(/[_-]/g, ' '))}.`;
          if (warning && payload.warning) {
            warning.textContent = payload.warning;
            warning.classList.remove('hidden');
          }
        } catch (error) {
          status.textContent = `Unable to load project fields: ${error.message}`;
        } finally {
          loadButton.disabled = false;
          loadButton.textContent = 'Load fields from Jira project';
        }
      });
    }
  }

  initUserPicker();
  initScanForm();
  initCustomCriteria();
})();

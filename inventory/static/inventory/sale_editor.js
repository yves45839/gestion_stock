/* Sales editor: UI calculations are advisory; the server validates every amount. */
(() => {
  'use strict';
  const form = document.getElementById('sale-form');
  if (!form) return;
  document.documentElement.classList.add('sale-js');
  const $ = (selector, root = document) => root.querySelector(selector);
  const $$ = (selector, root = document) => Array.from(root.querySelectorAll(selector));
  const products = JSON.parse($('#product-dataset').textContent);
  const customers = JSON.parse($('#customer-dataset').textContent);
  const productMap = new Map(products.map(p => [String(p.id), p]));
  const customerMap = new Map(customers.map(c => [String(c.id), c]));
  const body = $('#sale-lines-body'), search = $('#article-search'), results = $('#article-results');
  const customerPanel = $('#customer-panel'), customerSelect = $('#id_customer');
  const isQuote = form.dataset.isQuote === 'true';
  const money = cents => new Intl.NumberFormat('fr-FR', {maximumFractionDigits: 2}).format(cents / 100) + ' FCFA';
  const number = n => new Intl.NumberFormat('fr-FR', {maximumFractionDigits: 2}).format(n);
  const cents = value => Math.round((Number(String(value || 0).replace(',', '.')) || 0) * 100);
  const normalized = s => String(s || '').normalize('NFD').replace(/[\u0300-\u036f]/g, '').toLowerCase().trim();
  const rows = () => $$('.sale-line', body);
  let net = 0, hasMoneyError = false;
  function update() {
    let subtotal = 0, count = 0;
    const selectedQuantities = new Map();
    rows().forEach(row => {
      if (row.dataset.lineType !== 'product') return;
      const product = productMap.get($('.product-select', row).value);
      const qty = Number($('.quantity-input', row).value) || 0;
      const price = cents($('.unit-price-input', row).value);
      const total = qty * price;
      $('[data-line-total]', row).textContent = number(total / 100);
      subtotal += total;
      if (product) { count++; selectedQuantities.set(product.id, (selectedQuantities.get(product.id) || 0) + qty); }
    });
    const value = cents($('#id_discount_value').value);
    const kind = $('[name="discount_type"]:checked')?.value || 'percent';
    const discount = kind === 'percent' ? Math.round(subtotal * value / 10000) : value;
    net = Math.max(0, subtotal - discount);
    const paid = isQuote ? 0 : cents($('#id_amount_paid').value);
    const remaining = Math.max(0, net - paid);
    const customer = customerMap.get(customerSelect.value);
    const prior = cents(customer?.balance);
    $$('[data-subtotal]').forEach(e => e.textContent = money(subtotal));
    $$('[data-prior-balance]').forEach(e => e.textContent = money(prior));
    $('#discount-display').textContent = '−' + money(discount);
    $('#net-display').textContent = money(net);
    $('#remaining-display').textContent = money(remaining);
    $('#client-balance-display').textContent = money(prior + (isQuote ? 0 : remaining));
    $('#product-count').textContent = `${count} produit${count > 1 ? 's' : ''}`;
    const invalidDiscount = value < 0 || (kind === 'percent' ? value > 10000 : value > subtotal);
    hasMoneyError = invalidDiscount || paid > net;
    $('#money-error').textContent = invalidDiscount ? 'Vérifiez la remise : elle dépasse le montant autorisé.' : paid > net ? 'Le paiement dépasse le net à payer.' : '';
    const stockWarnings = products.filter(p => selectedQuantities.get(p.id) > Number(p.stock));
    $('#search-status').textContent = stockWarnings.length ? 'Attention : quantité supérieure au stock disponible pour ' + stockWarnings.map(p => p.name).join(', ') + '.' : '';
  }
  function reindex() {
    rows().forEach((row, i) => {
      row.dataset.formIndex = i;
      $$('input,select,textarea', row).forEach(input => {
        if (input.name) input.name = input.name.replace(/items-(?:\d+|__prefix__)-/, `items-${i}-`);
        if (input.id) input.id = input.id.replace(/items-(?:\d+|__prefix__)-/, `items-${i}-`);
      });
    });
    $('#id_items-TOTAL_FORMS').value = rows().length;
    update();
  }
  function renderProduct(row, product, resetPrice = false) {
    const info = $('[data-item-info]', row); info.replaceChildren();
    const stock = $('[data-stock]', row);
    if (!product) { stock.textContent = '—'; return; }
    if (product.image_url) { const image = document.createElement('img'); image.src = product.image_url; image.alt = ''; image.className = 'item-thumb'; info.append(image); }
    const meta = document.createElement('div'), name = document.createElement('strong'), ref = document.createElement('small');
    name.textContent = product.name; ref.textContent = product.reference || product.sku;
    meta.append(name, ref); info.append(meta);
    stock.textContent = number(Number(product.stock)); stock.classList.toggle('low', Number(product.stock) <= 3);
    stock.title = 'Stock disponible sur le site actif';
    if (resetPrice) $('.unit-price-input', row).value = product.sale_price;
  }
  function attach(row) {
    const type = $('.line-type-input', row);
    row.dataset.lineType = type.value || 'product';
    const select = $('.product-select', row);
    select.addEventListener('change', () => { renderProduct(row, productMap.get(select.value), true); update(); });
    $$('input', row).forEach(input => input.addEventListener('input', update));
    $$('[data-quantity]', row).forEach(button => button.addEventListener('click', () => {
      const input = $('.quantity-input', row); input.value = Math.max(1, (Number(input.value) || 0) + Number(button.dataset.quantity)); update();
    }));
    $('[data-remove-line]', row).addEventListener('click', () => { row.remove(); reindex(); });
    renderProduct(row, productMap.get(select.value));
  }
  function newRow(type = 'product') {
    const wrapper = document.createElement('tbody');
    wrapper.innerHTML = $('#sale-line-template').innerHTML.replace(/__prefix__/g, rows().length);
    const row = $('.sale-line', wrapper); $('.line-type-input', row).value = type;
    body.append(row); attach(row); reindex(); return row;
  }
  function addProduct(product) {
    let row = rows().find(r => r.dataset.lineType === 'product' && $('.product-select', r).value === String(product.id));
    if (row) $('.quantity-input', row).value = (Number($('.quantity-input', row).value) || 0) + 1;
    else {
      row = rows().find(r => r.dataset.lineType === 'product' && !$('.product-select', r).value) || newRow();
      $('.product-select', row).value = product.id; $('.quantity-input', row).value = 1;
      renderProduct(row, product, true);
    }
    search.value = ''; results.replaceChildren(); search.setAttribute('aria-expanded', 'false'); update(); search.focus();
  }
  const matches = term => products.filter(p => [p.name, p.sku, p.reference, p.barcode].some(v => normalized(v).includes(normalized(term)))).slice(0, 8);
  function renderResults() {
    results.replaceChildren();
    if (!search.value.trim()) { search.setAttribute('aria-expanded', 'false'); return; }
    const found = matches(search.value);
    if (!found.length) { const message = document.createElement('p'); message.textContent = 'Aucun produit trouvé.'; results.append(message); }
    found.forEach(p => {
      const button = document.createElement('button'); button.type = 'button'; button.setAttribute('role', 'option');
      const label = document.createElement('span'), ref = document.createElement('small'), price = document.createElement('span');
      label.textContent = p.name; ref.textContent = `${p.sku} · Stock : ${p.stock}`; label.append(ref); price.textContent = money(cents(p.sale_price)); button.append(label, price);
      button.addEventListener('click', () => addProduct(p));
      button.addEventListener('keydown', e => {
        if (e.key === 'ArrowDown') { e.preventDefault(); button.nextElementSibling?.focus(); }
        if (e.key === 'ArrowUp') { e.preventDefault(); (button.previousElementSibling || search).focus(); }
        if (e.key === 'Escape') { results.replaceChildren(); search.focus(); search.setAttribute('aria-expanded', 'false'); }
      });
      results.append(button);
    });
    search.setAttribute('aria-expanded', 'true');
  }
  search.addEventListener('input', renderResults);
  search.addEventListener('keydown', e => {
    if (e.key === 'Enter') {
      e.preventDefault();
      const exact = products.find(p => [p.barcode, p.sku, p.reference].some(v => v && normalized(v) === normalized(search.value)));
      const found = matches(search.value);
      if (exact || found.length === 1) addProduct(exact || found[0]); else renderResults();
    }
    if (e.key === 'ArrowDown') { e.preventDefault(); results.querySelector('button')?.focus(); }
    if (e.key === 'Escape') { results.replaceChildren(); search.setAttribute('aria-expanded', 'false'); }
  });
  document.addEventListener('click', e => { if (!e.target.closest('.article-search-panel')) { results.replaceChildren(); search.setAttribute('aria-expanded', 'false'); } });
  function selectCustomer() {
    const customer = customerMap.get(customerSelect.value);
    const name = $('#id_customer_name').value.trim();
    $('#customer-title').textContent = customer?.display_name || name || 'Sélectionnez un client';
    $('#customer-reference').textContent = customer?.reference || (name ? 'Nouveau client' : 'Client existant ou nouveau client');
    $('#choose-customer').textContent = customer || name ? 'Changer de client' : 'Choisir un client';
    const details = $('#customer-details'); details.hidden = !customer;
    if (customer) details.href = customer.url;
    update();
  }
  $('#choose-customer').addEventListener('click', () => { customerPanel.hidden = !customerPanel.hidden; if (!customerPanel.hidden) $('#customer-search').focus(); });
  $('#confirm-customer').addEventListener('click', () => { if ($('#id_customer_name').value.trim()) customerSelect.value = ''; selectCustomer(); customerPanel.hidden = true; });
  customerSelect.addEventListener('change', () => { $('#id_customer_name').value = ''; selectCustomer(); });
  $('#id_customer_name').addEventListener('input', () => { customerSelect.value = ''; selectCustomer(); });
  $('#customer-search').addEventListener('input', e => {
    const target = $('#customer-results'); target.replaceChildren(); const term = normalized(e.target.value); if (!term) return;
    customers.filter(c => [c.display_name, c.reference, c.phone].some(v => normalized(v).includes(term))).slice(0, 8).forEach(c => {
      const button = document.createElement('button'); button.type = 'button'; button.textContent = `${c.display_name} · ${c.reference}`;
      button.addEventListener('click', () => { customerSelect.value = c.id; $('#id_customer_name').value = ''; target.replaceChildren(); selectCustomer(); customerPanel.hidden = true; }); target.append(button);
    });
  });
  $$('[data-add-line]').forEach(button => button.addEventListener('click', () => {
    if (button.dataset.addLine === 'product') { search.focus(); return; }
    const row = newRow(button.dataset.addLine); $('.description-input', row).focus();
  }));
  $('#pay-all').addEventListener('click', () => { $('#id_amount_paid').value = (net / 100).toFixed(2); update(); });
  ['#id_discount_value', '#id_amount_paid'].forEach(selector => $(selector).addEventListener('input', update));
  $$('[name="discount_type"]').forEach(input => input.addEventListener('change', update));
  $('#id_sale_date').addEventListener('input', () => $('#document-date').textContent = $('#id_sale_date').value.replace('T', ' '));
  form.addEventListener('submit', e => {
    update();
    if (hasMoneyError) { e.preventDefault(); $('#money-error').scrollIntoView({block:'center'}); return; }
    if (e.submitter?.id !== 'preview-document') setTimeout(() => { $('#save-document').disabled = true; $('#save-document').textContent = 'Enregistrement…'; }, 0);
  });
  rows().forEach(attach);
  // Transfer only editable fields when switching sale/quote, never CSRF or submission tokens.
  const draftKey = 'lr-stock-sale-draft';
  try {
    const draft = JSON.parse(sessionStorage.getItem(draftKey) || 'null');
    if (draft?.target === location.pathname) {
      sessionStorage.removeItem(draftKey);
      while (rows().length < draft.count) newRow();
      draft.values.forEach(([name, value]) => {
        const input = form.elements.namedItem(name);
        if (!input) return;
        if (name === 'discount_type') { $$('[name="discount_type"]').forEach(r => r.checked = r.value === value); }
        else if (input.type === 'checkbox') input.checked = value === 'on';
        else if (input instanceof Element) input.value = value;
      });
      rows().forEach(row => { row.dataset.lineType = $('.line-type-input', row).value; renderProduct(row, productMap.get($('.product-select', row).value)); });
      if (isQuote) $('#id_amount_paid').value = '0';
    }
  } catch (_) { /* Storage may be unavailable; normal navigation still works. */ }
  $('#switch-kind')?.addEventListener('click', () => {
    const skip = new Set(['csrfmiddlewaretoken','submission_token','reference','document_kind','quote_id']);
    try { sessionStorage.setItem(draftKey, JSON.stringify({target:form.dataset.switchUrl,count:rows().length,values:Array.from(new FormData(form)).filter(([name]) => !skip.has(name))})); } catch (_) {}
    location.href = form.dataset.switchUrl;
  });
  reindex(); selectCustomer(); customerPanel.hidden = !$('.errorlist', customerPanel);
  $('#document-date').textContent = $('#id_sale_date').value.replace('T', ' ');
})();

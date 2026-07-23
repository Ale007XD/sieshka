/**
 * Time-First Menu System - Frontend
 * Handles slots, availability, sticky bar, and dynamic menu updates
 * Updated to use unified CartManager
 */

// Global state
const MenuState = {
    day: 'today',
    method: 'delivery',
    selectedSlot: null,
    slots: [],
    menuData: null
};

// Constants
const DAY_LABELS = {
    today: 'Сегодня',
    tomorrow: 'Завтра'
};

const METHOD_LABELS = {
    delivery: 'Доставка',
    pickup: 'Самовывоз'
};

const REASON_LABELS = {
    OUTSIDE_WINDOW: 'Вне времени приема заказов',
    LEAD_TIME: 'Требуется предзаказ',
    METHOD_NOT_ALLOWED: 'Недоступно для этого способа',
    TOMORROW_CUTOFF: 'Заказы на завтра до 23:00',
    INACTIVE: 'Временно недоступно',
    NO_RULE: 'Нет правил доступности'
};

const CTA_LABELS = {
    add_to_cart: 'Добавить',
    select_time: 'Выбрать время',
    preorder: 'Предзаказ',
    unavailable: 'Недоступно'
};

// ============================================================================
// Initialization
// ============================================================================

function waitForCartManager(callback) {
    if (typeof CartManager !== 'undefined') {
        callback();
    } else {
        setTimeout(() => waitForCartManager(callback), 50);
    }
}

document.addEventListener('DOMContentLoaded', () => {
    initStickyBar();
    loadMenu();
    initOtherDateModal();
    
    // Wait for CartManager and setup event delegation
    waitForCartManager(() => {
        setupProductEventDelegation();
        syncAllProductControls();
    });
});

// ============================================================================
// Event Delegation for Cart Actions
// ============================================================================

function setupProductEventDelegation() {
    const menuRoot = document.getElementById('menu-root');
    const menuContainer = document.getElementById('menu-container');
    if (!menuContainer) return;
    
    menuContainer.addEventListener('click', (e) => {
        const addBtn = e.target.closest('.btn-add-to-cart');
        const qtyBtn = e.target.closest('.qty-btn');
        
        // Handle Add to Cart buttons
        if (addBtn) {
            e.preventDefault();
            const productCard = addBtn.closest('[data-product-id]');
            
            // Check for preorder action
             if (addBtn.dataset.action === 'preorder') {
                 showPreorderInfo(addBtn.dataset.productId);
                 return;
             }
            
            // Check for scroll to slot action
            if (addBtn.dataset.action === 'scroll-slot') {
                scrollToSlotSelector();
                return;
            }
            
            // Standard add to cart
            if (productCard) {
                const productId = productCard.dataset.productId;
                const price = parseInt(productCard.dataset.price, 10);
                const name = productCard.dataset.name;
                const leadTimeMinutes = parseInt(productCard.dataset.leadTimeMinutes || '0', 10);
                addToCartWithQty(productId, price, name, leadTimeMinutes);
            }
        }
        
        // Handle Quantity +/- buttons
        if (qtyBtn) {
            e.preventDefault();
            const productCard = qtyBtn.closest('[data-product-id]');
            if (productCard) {
                const productId = productCard.dataset.productId;
                const delta = qtyBtn.classList.contains('qty-btn-minus') ? -1 : 1;
                updateProductQty(productId, delta);
            }
        }
    });
}

// ============================================================================
// Sticky Bar
// ============================================================================

function initStickyBar() {
    const stickyBar = document.getElementById('sticky-bar');
    if (!stickyBar) return;

    // Detect initial method from active button in UI
    const activeBtn = stickyBar.querySelector('[data-method].active');
    if (activeBtn) {
        MenuState.method = activeBtn.dataset.method;
    }

    // Day toggle - TEMPORARILY DISABLED
    // const dayButtons = stickyBar.querySelectorAll('[data-day]');
    // dayButtons.forEach(btn => {
    //     btn.addEventListener('click', (e) => {
    //         e.preventDefault();
    //         const day = btn.dataset.day;
    //         setDay(day);
    //
    //         // Update active state
    //         dayButtons.forEach(b => b.classList.remove('active'));
    //         btn.classList.add('active');
    //     });
    // });

    // Method toggle
    const methodButtons = stickyBar.querySelectorAll('[data-method]');
    methodButtons.forEach(btn => {
        btn.addEventListener('click', (e) => {
            e.preventDefault();
            const method = btn.dataset.method;
            setMethod(method);

            // Update active state with proper Bootstrap classes
            methodButtons.forEach(b => {
                b.classList.remove('btn-brand', 'active');
                b.classList.add('btn-outline-brand');
            });
            btn.classList.remove('btn-outline-brand');
            btn.classList.add('btn-brand', 'active');
        });
    });

    // Slot selector - TEMPORARILY DISABLED
    // const slotSelect = document.getElementById('slot-select');
    // if (slotSelect) {
    //     slotSelect.addEventListener('change', (e) => {
    //         setSlot(e.target.value || null);
    //     });
    // }
}

function setMethod(method) {
    MenuState.method = method;
    loadMenu();
    updateStickyBarUI();
}

function updateStickyBarUI() {
    document.querySelectorAll('[data-method]').forEach(btn => {
        btn.classList.toggle('active', btn.dataset.method === MenuState.method);
    });
}

// ============================================================================
// API Calls
// ============================================================================

async function loadMenu() {
    try {
        // Simplified: only method parameter, day/slot temporarily disabled
        let url = `/api/menu?method=${MenuState.method}`;

        const response = await fetch(url);
        
        if (!response.ok) throw new Error('Failed to load menu');
        
        const data = await response.json();
        MenuState.menuData = data;
        renderMenu(data);
        
        // Скрываем статус загрузки после рендера
        const menuStatus = document.getElementById('menu-status');
        if (menuStatus) menuStatus.style.display = 'none';
        
        const menuRoot = document.getElementById('menu-root');
        if (menuRoot) menuRoot.style.display = '';
        
        // Populate upsell suggestions from categories like "Напитки" or "Соусы"
        if (typeof CartManager !== 'undefined' && data.categories) {
            const upsellCats = data.categories.filter(c => 
                /напитки|соусы|аксессуары|десерт/i.test(c.name)
            );
            const upsellItems = [];
            upsellCats.forEach(cat => {
                upsellItems.push(...cat.products.slice(0, 2));
            });
            CartManager.setUpsellSuggestions(upsellItems.slice(0, 6));
        }
        
        // Sync product controls after rendering
        if (typeof CartManager !== 'undefined') {
            setTimeout(() => CartManager.updateProductControls(), 0);
        }
    } catch (error) {
        console.error('Error loading menu:', error);
        const container = document.getElementById('menu-container');
        if (container) {
            container.innerHTML = `
                <div class="alert alert-danger">
                    <i class="bi bi-exclamation-triangle me-2"></i>
                    Ошибка загрузки меню. Пожалуйста, попробуйте позже.
                </div>
            `;
        }
    }
}

// ============================================================================
// Menu Rendering - обновление существующих карточек (НЕ перерисовка)
// ============================================================================

function renderMenu(data) {
    // Обновляем badge и CTA существующих карточек
    if (!data.categories) return;

    // Если в DOM нет ни одной карточки товара — создаём их из API
    const menuContainer = document.getElementById('menu-container');
    const hasExistingCards = menuContainer && menuContainer.querySelector('.product-card[data-product-id]');
    if (menuContainer && !hasExistingCards) {
        // Первичная отрисовка: строим DOM из данных API
        menuContainer.innerHTML = '';
        data.categories.forEach(category => {
            const section = createCategoryElement(category);
            menuContainer.appendChild(section);
        });
        return;
    }

    data.categories.forEach(category => {
        let hasVisibleProducts = false;

        category.products.forEach(product => {
            updateProductCard(product);
            if (product.available || product.cta_type === 'preorder') {
                hasVisibleProducts = true;
            }
        });

        // Hide/show category based on visible products AND current category filter
        const categorySection = document.getElementById(`category-${category.category_id}`);
        if (categorySection) {
            const activeCategoryBtn = document.querySelector('.category-btn.active');
            const activeCategoryId = activeCategoryBtn ? activeCategoryBtn.dataset.categoryId : 'all';
            
            if (activeCategoryId !== 'all' && String(category.category_id) !== String(activeCategoryId)) {
                categorySection.style.display = 'none';
            } else {
                categorySection.style.display = hasVisibleProducts ? '' : 'none';
            }
        }
    });

    // Обновляем upsell suggestions
    updateUpsellSuggestions(data);
    
    // Заполняем кнопки категорий из API (если контейнер пуст)
    populateCategoryButtons(data.categories);
}

function populateCategoryButtons(categories) {
    const container = document.getElementById('categoryButtons');
    if (!container) return;
    // Если уже есть кнопки — не дублируем
    if (container.querySelector('.category-btn')) return;
    
    // Привязываем обработчик к существующей кнопке "Все меню"
    const allBtn = document.querySelector('.category-btn[data-category-id="all"]');
    if (allBtn && !allBtn._bound) {
        allBtn._bound = true;
        allBtn.addEventListener('click', (e) => {
            e.preventDefault();
            filterByCategory('all');
        });
    }

    const activeCategoryBtn = document.querySelector('.category-btn.active');
    const initialActiveId = activeCategoryBtn ? activeCategoryBtn.dataset.categoryId : 'all';

    categories.forEach(cat => {
        const btn = document.createElement('button');
        btn.className = 'btn btn-sm rounded-pill category-btn';
        btn.dataset.categoryId = cat.category_id;
        btn.textContent = cat.name;
        if (String(cat.category_id) === String(initialActiveId)) {
            btn.classList.add('active');
        }
        btn.addEventListener('click', (e) => {
            e.preventDefault();
            filterByCategory(cat.category_id);
        });
        container.appendChild(btn);
    });
}

function filterByCategory(categoryId) {
    document.querySelectorAll('.category-btn').forEach(b => {
        b.classList.toggle('active', String(b.dataset.categoryId) === String(categoryId));
    });
    // Показываем/скрываем секции категорий
    document.querySelectorAll('[id^="category-"]').forEach(section => {
        if (String(categoryId) === 'all') {
            section.style.display = '';
        } else {
            const sid = section.id.replace('category-', '');
            section.style.display = (sid === String(categoryId)) ? '' : 'none';
        }
    });
}

function updateProductCard(product) {
    const card = document.querySelector(`.product-card[data-product-id="${product.product_id}"]`);
    if (!card) return;

    // Update availability state
    card.dataset.available = product.available;
    card.dataset.leadTimeMinutes = Number(product.lead_time_minutes || 0);
    card.classList.toggle('unavailable', !product.available);

    // Visibility: hide only truly unavailable (OUTSIDE_WINDOW, NO_RULE, etc.)
    // Products with cta_type='preorder' stay visible — they can be ordered in advance
    const isHidden = !product.available && product.cta_type !== 'preorder';
    card.style.display = isHidden ? 'none' : '';

    // Update badge
    const badgeContainer = card.querySelector('.availability-badge');
    if (badgeContainer) {
        let badgeHtml = '';
        if (product.badge_text) {
            const badgeClass = product.available ? 'bg-success' : 'bg-secondary';
            badgeHtml += `<span class="badge ${badgeClass} me-1">${escapeHtml(product.badge_text)}</span>`;
        }
        if (product.next_available) {
            badgeHtml += `<small class="text-muted d-block mt-1">${escapeHtml(product.next_available)}</small>`;
        }
        badgeContainer.innerHTML = badgeHtml;
    }

    // Update CTA button
    const addBtn = card.querySelector('.btn-add-to-cart');
    const qtyControl = card.querySelector('.product-controls');

    if (!product.available) {
        // preorder: show button, hide qty control
        if (product.cta_type === 'preorder') {
            if (addBtn) {
                addBtn.style.display = 'block';
                addBtn.disabled = false;
                addBtn.dataset.action = 'preorder';
                addBtn.dataset.productId = product.product_id;
                addBtn.innerHTML = CTA_LABELS['preorder'];
            }
            if (qtyControl) qtyControl.style.display = 'none';
        } else {
            // Truly unavailable — hide both
            if (addBtn) addBtn.style.display = 'none';
            if (qtyControl) qtyControl.style.display = 'none';
        }
    } else {
        // Available — let CartManager decide btn vs qty control
        if (typeof CartManager !== 'undefined') {
            const qty = CartManager.getItemQty(product.product_id);
            if (qty > 0) {
                if (addBtn) addBtn.style.display = 'none';
                if (qtyControl) {
                    qtyControl.style.display = 'flex';
                    qtyControl.classList.remove('d-none');
                }
            } else {
                if (addBtn) {
                    addBtn.style.display = 'block';
                    addBtn.disabled = false;
                    addBtn.dataset.action = '';
                    addBtn.innerHTML = '<i class="bi bi-plus-lg me-1"></i>Добавить';
                }
                if (qtyControl) qtyControl.style.display = 'none';
            }
        }
    }
}

function updateUpsellSuggestions(data) {
    // Populate upsell suggestions from categories like "Напитки" or "Соусы"
    if (typeof CartManager === 'undefined' || !data.categories) return;
    
    const upsellCats = data.categories.filter(c => 
        /напитки|соусы|аксессуары|десерт/i.test(c.name)
    );
    const upsellItems = [];
    upsellCats.forEach(cat => {
        upsellItems.push(...cat.products.filter(p => p.available).slice(0, 2));
    });
    CartManager.setUpsellSuggestions(upsellItems.slice(0, 6));
    
    // Set "add for later" items (unavailable now but available later)
    const laterItems = getUnavailableForCurrentSlot();
    CartManager.setAddForLaterItems(laterItems);
}

function createCategoryElement(category) {
    const section = document.createElement('section');
    section.className = 'menu-category mb-5';
    section.id = `category-${category.category_id}`;
    section.dataset.catId = category.category_id;
    
    const header = document.createElement('h2');
    header.className = 'h4 mb-3';
    header.textContent = category.name;
    section.appendChild(header);
    
    const productsGrid = document.createElement('div');
    productsGrid.className = 'row g-3';
    
    category.products.forEach(product => {
        const productEl = createProductElement(product);
        productsGrid.appendChild(productEl);
    });
    
    section.appendChild(productsGrid);
    return section;
}

function createProductElement(product) {
    const col = document.createElement('div');
    col.className = 'col-6 col-md-4 col-lg-3';
    
    const isAvailable = product.available;
    const ctaType = product.cta_type;
    const leadTimeMinutes = Number(product.lead_time_minutes || 0);
    
    // Build product card with data attributes for cart integration
    col.innerHTML = `
        <div class="card h-100 product-card ${isAvailable ? '' : 'unavailable'}" 
             data-product-id="${product.product_id}"
             data-name="${escapeHtml(product.name)}"
             data-price="${product.price_rub}"
             data-available="${isAvailable}"
             data-lead-time-minutes="${leadTimeMinutes}">
            <div class="card-body">
                <h5 class="card-title h6">${escapeHtml(product.name)}</h5>
                <p class="card-text">
                    <span class="price">${product.price_rub} ₽</span>
                </p>
                
                ${product.badge_text ? `
                    <span class="badge ${isAvailable ? 'bg-success' : 'bg-secondary'}">
                        ${escapeHtml(product.badge_text)}
                    </span>
                ` : ''}
                
                ${product.next_available ? `
                    <small class="d-block text-muted mt-1">
                        ${escapeHtml(product.next_available)}
                    </small>
                ` : ''}
                
                ${product.reason_code ? `
                    <small class="d-block text-muted mt-1 reason-code">
                        ${REASON_LABELS[product.reason_code] || product.reason_code}
                    </small>
                ` : ''}
            </div>
            
            <div class="card-footer">
                ${renderProductControls(product, isAvailable, ctaType)}
            </div>
        </div>
    `;
    
    // Add event listeners for quantity controls
    setupProductControls(col, product, isAvailable, ctaType);
    
    return col;
}

function renderProductControls(product, isAvailable, ctaType) {
    if (!isAvailable) {
        return `
            <button class="btn btn-unavailable btn-sm" disabled>
                ${CTA_LABELS[ctaType] || 'Недоступно'}
            </button>
        `;
    }
    
    if (ctaType === 'select_time') {
        return `
            <button class="btn btn-outline-brand btn-add-to-cart btn-sm w-100" 
                    id="add-btn-${product.product_id}"
                    data-action="scroll-slot">
                ${CTA_LABELS[ctaType]}
            </button>
        `;
    }
    
    if (ctaType === 'preorder') {
        return `
            <button class="btn btn-outline-brand btn-add-to-cart btn-sm w-100" 
                    id="add-btn-${product.product_id}"
                    data-action="preorder" data-product-id="${product.product_id}">
                ${CTA_LABELS[ctaType]}
            </button>
        `;
    }
    
    // Default: add to cart with quantity controls
    const productId = product.product_id;
    const priceRub = product.price_rub;
    const name = product.name;
    
    return `
        <button class="btn btn-brand btn-add-to-cart btn-sm w-100" 
                id="add-btn-${productId}"
                onclick="addToCartWithQty(${productId}, ${priceRub}, '${escapeJs(name)}', ${Number(product.lead_time_minutes || 0)})">
            ${CTA_LABELS[ctaType] || 'Добавить'}
        </button>
        <div class="product-controls d-none justify-content-center align-items-center gap-2" 
             id="qty-control-${productId}"
             data-product-id="${productId}" data-price="${priceRub}" data-name="${escapeHtml(name)}">
            <button type="button" class="qty-btn qty-btn-minus" 
                    aria-label="Уменьшить количество">
                −
            </button>
            <span class="qty-display fw-bold" id="qty-display-${productId}" style="min-width: 24px; text-align: center;">0</span>
            <button type="button" class="qty-btn" 
                    aria-label="Увеличить количество">
                +
            </button>
        </div>
    `;
}

function setupProductControls(col, product, isAvailable, ctaType) {
    // Now handled by syncAllProductControls() after CartManager is ready
}

// ============================================================================
// Cart Integration Functions
// ============================================================================

function addToCartWithQty(productId, priceRub, name, leadTimeMinutes = 0) {
    if (typeof CartManager === 'undefined') {
        console.error('CartManager not loaded');
        return;
    }
    
    // Add item to cart
    const success = CartManager.addItem(productId, priceRub, name, leadTimeMinutes);
    
    if (success) {
        // Update UI to show quantity controls
        syncProductControl(productId);
        
        // Trigger cart animation in navbar
        triggerCartAnimation();
    }
}

function updateProductQty(productId, delta) {
    if (typeof CartManager === 'undefined') {
        console.error('CartManager not loaded');
        return;
    }
    
    const success = CartManager.updateQty(productId, delta);
    
    if (success) {
        syncProductControl(productId);
    }
}

function syncProductControl(productId) {
    if (typeof CartManager === 'undefined') return;
    
    const qty = CartManager.getItemQty(productId);
    const addBtn = document.getElementById(`add-btn-${productId}`);
    const qtyControl = document.getElementById(`qty-control-${productId}`);
    const qtyDisplay = document.getElementById(`qty-display-${productId}`);
    
    if (!addBtn || !qtyControl) return;
    
    if (qty > 0) {
        addBtn.classList.add('d-none');
        qtyControl.classList.remove('d-none');
        if (qtyDisplay) {
            qtyDisplay.textContent = qty;
        }
        
        // Update button states based on limits
        const plusBtn = qtyControl.querySelector('.qty-btn:last-child');
        if (plusBtn) {
            plusBtn.disabled = qty >= CartManager.QTY_MAX;
        }
    } else {
        addBtn.classList.remove('d-none');
        qtyControl.classList.add('d-none');
    }
}

function syncAllProductControls() {
    if (typeof CartManager === 'undefined') return;
    
    document.querySelectorAll('[data-product-id]').forEach(card => {
        const productId = card.dataset.productId;
        if (productId) {
            syncProductControl(productId);
        }
    });
}

// ============================================================================
// UI Helpers
// ============================================================================

function scrollToSlotSelector() {
    const selector = document.getElementById('slot-select');
    if (selector) {
        selector.scrollIntoView({ behavior: 'smooth', block: 'center' });
        selector.focus();
        
        // Highlight the selector temporarily
        selector.classList.add('is-invalid');
        setTimeout(() => selector.classList.remove('is-invalid'), 1000);
    }
}

function showPreorderInfo(productId) {
    let leadText = 'предзаказ';
    if (MenuState.menuData) {
        for (const cat of MenuState.menuData.categories) {
            const p = cat.products.find(p => p.product_id === productId);
            if (p && p.badge_text) { leadText = p.badge_text; break; }
        }
    }
    showNotification(`Этот товар требует предзаказа минимум за ${leadText}. Выберите время доставки.`, 'info');
}

function triggerCartAnimation() {
    const cartBtn = document.getElementById('navbarCartBtn');
    if (cartBtn) {
        cartBtn.classList.add('bounce');
        setTimeout(() => cartBtn.classList.remove('bounce'), 500);
    }
}

function showNotification(message, type = 'info') {
    // Use CartManager toast if available
    if (typeof CartManager !== 'undefined' && CartManager.showToast) {
        CartManager.showToast(message, type);
        return;
    }
    
    // Fallback to simple notification
    const notification = document.createElement('div');
    notification.className = `alert alert-${type} alert-dismissible fade show position-fixed`;
    notification.style.cssText = 'top: 80px; right: 20px; z-index: 9999; max-width: 300px;';
    notification.innerHTML = `
        ${escapeHtml(message)}
        <button type="button" class="btn-close" data-bs-dismiss="alert"></button>
    `;
    
    document.body.appendChild(notification);
    
    setTimeout(() => {
        notification.remove();
    }, 3000);
}

// ============================================================================
// Other Date Modal
// ============================================================================

function initOtherDateModal() {
    const modal = document.getElementById('otherDateModal');
    if (!modal) return;
    
    // Update cart preview when modal opens
    modal.addEventListener('show.bs.modal', updateModalCartPreview);
    
    // Update WhatsApp link with cart text
    const whatsappBtn = document.getElementById('whatsappOrderBtn');
    if (whatsappBtn) {
        whatsappBtn.addEventListener('click', function(e) {
            const cartText = generateCartTextForMessage();
            this.href = `https://wa.me/79641027995?text=${encodeURIComponent(cartText)}`;
        });
    }
}

function updateModalCartPreview() {
    const previewContainer = document.getElementById('modalCartPreview');
    if (!previewContainer || typeof CartManager === 'undefined') return;
    
    const items = CartManager.loadCart();
    if (items.length === 0) {
        previewContainer.innerHTML = '<em class="text-muted">Корзина пуста</em>';
        return;
    }
    
    let html = '<ul class="list-unstyled mb-0">';
    items.forEach(item => {
        html += `<li>${escapeHtml(item.name)} × ${item.qty} = ${item.price_rub * item.qty} ₽</li>`;
    });
    html += '</ul>';
    
    const total = items.reduce((sum, item) => sum + (item.price_rub * item.qty), 0);
    html += `<div class="mt-2 pt-2 border-top fw-bold">Итого: ${total} ₽</div>`;
    
    previewContainer.innerHTML = html;
}

function generateCartTextForMessage() {
    if (typeof CartManager === 'undefined') return 'Здравствуйте! Хочу сделать предзаказ.';
    
    const items = CartManager.loadCart();
    if (items.length === 0) {
        return 'Здравствуйте! Хочу сделать предзаказ на другую дату.';
    }
    
    let text = 'Здравствуйте! Хочу сделать предзаказ:\n\n';
    items.forEach(item => {
        text += `• ${item.name} × ${item.qty} = ${item.price_rub * item.qty} ₽\n`;
    });
    
    const total = items.reduce((sum, item) => sum + (item.price_rub * item.qty), 0);
    text += `\nИтого: ${total} ₽\n`;
    text += '\nПрошу связаться для уточнения даты и времени доставки.';
    
    return text;
}

// ============================================================================
// Upsell for "add for later" feature
// ============================================================================

function getUnavailableForCurrentSlot() {
    // Returns products that are unavailable now but available later/tomorrow
    if (!MenuState.menuData || !MenuState.menuData.categories) return [];
    
    const unavailable = [];
    MenuState.menuData.categories.forEach(cat => {
        cat.products.forEach(product => {
            if (!product.available && product.next_available) {
                unavailable.push({
                    product_id: product.product_id,
                    name: product.name,
                    price_rub: product.price_rub,
                    next_available: product.next_available,
                    lead_time_minutes: Number(product.lead_time_minutes || 0)
                });
            }
        });
    });
    
    return unavailable.slice(0, 4); // Limit to 4 items
}

// ============================================================================
// Utilities
// ============================================================================

function escapeHtml(text) {
    if (!text) return '';
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

function escapeJs(text) {
    if (!text) return '';
    return String(text)
        .replace(/'/g, "\\'")
        .replace(/"/g, '\\"');
}

// Expose necessary functions globally
window.addToCartWithQty = addToCartWithQty;
window.updateProductQty = updateProductQty;
window.scrollToSlotSelector = scrollToSlotSelector;
window.showPreorderInfo = showPreorderInfo;
window.getUnavailableForCurrentSlot = getUnavailableForCurrentSlot;

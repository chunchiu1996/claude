-- All money is stored as integer cents (USD).

CREATE TABLE IF NOT EXISTS suppliers (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    location TEXT,
    contact_name TEXT,
    contact_email TEXT,
    contact_phone TEXT,              -- WeChat / WhatsApp / phone
    commission_rate REAL NOT NULL DEFAULT 0.20
        CHECK (commission_rate >= 0 AND commission_rate < 1),
    portal_token TEXT NOT NULL UNIQUE,
    notes TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS categories (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    slug TEXT NOT NULL UNIQUE,
    description TEXT,
    sort_order INTEGER NOT NULL DEFAULT 100
);

CREATE TABLE IF NOT EXISTS products (
    id INTEGER PRIMARY KEY,
    sku TEXT NOT NULL UNIQUE,
    slug TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    category_id INTEGER REFERENCES categories(id),
    supplier_id INTEGER REFERENCES suppliers(id),
    description TEXT,
    specs TEXT,                      -- one "Key: Value" per line
    unit TEXT NOT NULL DEFAULT 'piece',
    unit_note TEXT,                  -- e.g. "23.64 sq ft per box"
    price_cents INTEGER NOT NULL CHECK (price_cents >= 0),
    compare_at_cents INTEGER,        -- typical big-box retail price
    moq INTEGER NOT NULL DEFAULT 1 CHECK (moq >= 1),
    stock_qty INTEGER NOT NULL DEFAULT 0 CHECK (stock_qty >= 0),
    warehouse TEXT,
    image_url TEXT,
    active INTEGER NOT NULL DEFAULT 1,
    featured INTEGER NOT NULL DEFAULT 0,
    quote_only INTEGER NOT NULL DEFAULT 0,   -- hide price; customers request a quote
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_products_category ON products(category_id);
-- SKUs are matched case-insensitively (imports, photo uploads); without this every lookup scans the table.
CREATE INDEX IF NOT EXISTS idx_products_sku_nocase ON products(sku COLLATE NOCASE);
CREATE INDEX IF NOT EXISTS idx_products_supplier ON products(supplier_id);

-- Extra product photos (the main photo is products.image_url). position 2, 3, ... from SKU-2.jpg, SKU-3.jpg
CREATE TABLE IF NOT EXISTS product_images (
    id INTEGER PRIMARY KEY,
    product_id INTEGER NOT NULL REFERENCES products(id) ON DELETE CASCADE,
    position INTEGER NOT NULL CHECK (position >= 2),
    url TEXT NOT NULL,
    UNIQUE (product_id, position)
);

-- Failed admin logins, for rate limiting password guessing.
CREATE TABLE IF NOT EXISTS login_attempts (
    id INTEGER PRIMARY KEY,
    ip TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_login_attempts_ip ON login_attempts(ip, created_at);

-- Volume pricing: buying >= min_qty units gets price_cents per unit.
CREATE TABLE IF NOT EXISTS price_tiers (
    id INTEGER PRIMARY KEY,
    product_id INTEGER NOT NULL REFERENCES products(id) ON DELETE CASCADE,
    min_qty INTEGER NOT NULL CHECK (min_qty >= 2),
    price_cents INTEGER NOT NULL CHECK (price_cents >= 0),
    UNIQUE (product_id, min_qty)
);

-- Every change to stock_qty is logged here so each factory can audit its consignment.
CREATE TABLE IF NOT EXISTS stock_movements (
    id INTEGER PRIMARY KEY,
    product_id INTEGER NOT NULL REFERENCES products(id) ON DELETE CASCADE,
    qty_change INTEGER NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('receive', 'sale', 'cancel', 'adjust')),
    reference TEXT,                  -- container #, order #, ...
    note TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_movements_product ON stock_movements(product_id);

CREATE TABLE IF NOT EXISTS orders (
    id INTEGER PRIMARY KEY,
    number TEXT NOT NULL UNIQUE,
    access_token TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending_payment'
        CHECK (status IN ('pending_payment', 'paid', 'fulfilled', 'cancelled')),
    payment_method TEXT NOT NULL CHECK (payment_method IN ('card', 'invoice')),
    customer_name TEXT NOT NULL,
    email TEXT NOT NULL,
    phone TEXT,
    company TEXT,
    customer_type TEXT,
    fulfillment TEXT NOT NULL CHECK (fulfillment IN ('pickup', 'delivery')),
    address TEXT,
    city TEXT,
    state TEXT,
    zip TEXT,
    notes TEXT,
    subtotal_cents INTEGER NOT NULL,
    shipping_cents INTEGER NOT NULL DEFAULT 0,
    total_cents INTEGER NOT NULL,
    stripe_session_id TEXT,
    admin_note TEXT,
    invoice_sent_at TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS order_items (
    id INTEGER PRIMARY KEY,
    order_id INTEGER NOT NULL REFERENCES orders(id) ON DELETE CASCADE,
    product_id INTEGER REFERENCES products(id),
    supplier_id INTEGER REFERENCES suppliers(id),
    sku TEXT NOT NULL,
    name TEXT NOT NULL,
    unit TEXT,
    qty INTEGER NOT NULL CHECK (qty > 0),
    unit_price_cents INTEGER NOT NULL,
    line_total_cents INTEGER NOT NULL,
    commission_rate REAL NOT NULL DEFAULT 0   -- snapshot at time of sale
);
CREATE INDEX IF NOT EXISTS idx_order_items_order ON order_items(order_id);
CREATE INDEX IF NOT EXISTS idx_order_items_supplier ON order_items(supplier_id);

-- Bulk/trade quote requests (kind='quote') and factory applications (kind='supplier').
CREATE TABLE IF NOT EXISTS inquiries (
    id INTEGER PRIMARY KEY,
    kind TEXT NOT NULL DEFAULT 'quote' CHECK (kind IN ('quote', 'supplier')),
    reference TEXT,                  -- e.g. Q-260927-01234, quoted by the customer on WhatsApp/email
    access_token TEXT,               -- lets the customer reopen their request page
    contact_pref TEXT,               -- email | whatsapp | phone | wechat
    source TEXT,                     -- marketing source, e.g. "edm / 2026-10-factory-invite" (from utm_ links)
    name TEXT NOT NULL,
    email TEXT NOT NULL,
    phone TEXT,
    company TEXT,
    role TEXT,
    location TEXT,
    items TEXT,
    message TEXT,
    status TEXT NOT NULL DEFAULT 'new'
        CHECK (status IN ('new', 'contacted', 'quoted', 'won', 'lost')),
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Products listed on a quote request (no prices: we reply with pricing).
CREATE TABLE IF NOT EXISTS inquiry_items (
    id INTEGER PRIMARY KEY,
    inquiry_id INTEGER NOT NULL REFERENCES inquiries(id) ON DELETE CASCADE,
    product_id INTEGER REFERENCES products(id),
    sku TEXT NOT NULL,
    name TEXT NOT NULL,
    unit TEXT,
    qty INTEGER NOT NULL CHECK (qty > 0)
);
CREATE INDEX IF NOT EXISTS idx_inquiry_items_inquiry ON inquiry_items(inquiry_id);

-- Money sent to a factory for its share of consignment sales.
CREATE TABLE IF NOT EXISTS payouts (
    id INTEGER PRIMARY KEY,
    supplier_id INTEGER NOT NULL REFERENCES suppliers(id),
    amount_cents INTEGER NOT NULL CHECK (amount_cents > 0),
    reference TEXT,
    note TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

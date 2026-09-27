"""Demo data so the shop isn't empty on first run. All suppliers/products here are fictional."""
import secrets

from . import orders
from .catalog import PRODUCT_SELECT, get_or_create_category, record_movement, set_tiers, slugify, unique_slug

SUPPLIERS = [
    ("Foshan Ceramic Works (Demo)", "Foshan, Guangdong", 0.18),
    ("Jiangsu SPC Flooring Mill (Demo)", "Changzhou, Jiangsu", 0.20),
    ("Kaiping Sanitary Ware Factory (Demo)", "Kaiping, Guangdong", 0.22),
    ("Zhongshan Lighting Factory (Demo)", "Zhongshan, Guangdong", 0.22),
    ("Shandong Cabinet Factory (Demo)", "Linyi, Shandong", 0.20),
    ("Yongkang Hardware Factory (Demo)", "Yongkang, Zhejiang", 0.20),
]

CATEGORIES = [
    ("Flooring", "Waterproof SPC vinyl plank and laminate."),
    ("Tile", "Porcelain floor & wall tile and natural stone mosaics."),
    ("Kitchen Cabinets", "Solid-wood-frame shaker cabinets, soft-close hardware."),
    ("Bathroom Vanities", "Freestanding vanities with quartz tops."),
    ("Faucets & Fixtures", "Kitchen and bath faucets, shower systems."),
    ("Lighting", "LED recessed lights and fixtures."),
    ("Doors & Hardware", "Door levers, barn door kits and more."),
]

# sku, name, category, supplier index, unit, unit_note, price, compare_at, moq, stock, tiers, featured, description, specs
PRODUCTS = [
    ("SPC-7MM-OAK", "Rigid Core SPC Vinyl Plank – Natural Oak, 7mm with Pad", "Flooring", 1, "box",
     "23.64 sq ft per box", 4250, 6600, 1, 860, [(20, 3899), (100, 3545)], 1,
     "100% waterproof stone-polymer core plank with click-lock install and pre-attached IXPE underlayment. "
     "Great for kitchens, basements and rentals.",
     "Thickness: 7mm (incl. 1.5mm pad)\nWear layer: 20 mil\nPlank size: 7.2 x 60 in\nFinish: EIR textured\n"
     "Install: Floating click-lock\nWarranty: Lifetime residential / 15-yr commercial"),
    ("SPC-7MM-GRY", "Rigid Core SPC Vinyl Plank – Coastal Grey, 7mm with Pad", "Flooring", 1, "box",
     "23.64 sq ft per box", 4250, 6600, 1, 640, [(20, 3899), (100, 3545)], 0,
     "Cool grey washed-oak look in a fully waterproof rigid core plank.",
     "Thickness: 7mm (incl. 1.5mm pad)\nWear layer: 20 mil\nPlank size: 7.2 x 60 in\nInstall: Floating click-lock"),
    ("LAM-12MM-HIC", "12mm AC4 Laminate – Rustic Hickory", "Flooring", 1, "box", "16.5 sq ft per box", 3600, 5400,
     1, 420, [(30, 3300)], 0,
     "Thick, quiet 12mm laminate with painted bevels and a scratch-resistant AC4 surface.",
     "Thickness: 12mm\nAbrasion rating: AC4\nPlank size: 7.6 x 48 in\nWater resistance: 72 hr"),
    ("TIL-POR-2448-CAL", "Porcelain Tile 24x48 in – Calacatta Polished", "Tile", 0, "box", "2 pcs / 16 sq ft per box",
     4960, 8800, 1, 1150, [(30, 4480), (120, 4000)], 1,
     "Large-format glazed porcelain with bookmatch-style Calacatta veining and a mirror polish.",
     "Size: 24 x 48 in\nThickness: 9mm\nFinish: Polished\nPEI rating: 3\nRectified edges: Yes\nUse: Floor & wall"),
    ("TIL-POR-1224-CEM", "Porcelain Tile 12x24 in – Cement Grey Matte", "Tile", 0, "box", "8 pcs / 16 sq ft per box",
     2880, 4400, 1, 2300, [(50, 2560), (200, 2240)], 0,
     "Concrete-look matte porcelain, R10 slip rating – ideal for floors, showers and commercial spaces.",
     "Size: 12 x 24 in\nThickness: 9mm\nFinish: Matte R10\nPEI rating: 4\nUse: Floor, wall, shower floor"),
    ("TIL-MOS-HEX-WHT", "Marble Hexagon Mosaic – Carrara White", "Tile", 0, "sheet", "~0.97 sq ft per sheet",
     1150, 1900, 10, 3400, [(100, 990)], 0,
     "Honed Carrara marble 2-inch hex mosaic on mesh for bathroom floors and backsplashes.",
     "Chip size: 2 in hex\nMaterial: Natural marble\nFinish: Honed\nSheet size: 11.4 x 12 in"),
    ("CAB-SHK-W-B30", "Shaker White Base Cabinet 30 in – Soft-Close", "Kitchen Cabinets", 4, "piece",
     "30W x 34.5H x 24D in, assembled", 18900, 32900, 1, 140, [(10, 17500)], 1,
     "Solid birch face frame and doors, 3/4 in plywood box, full-extension soft-close drawer and doors.",
     "Size: 30W x 34.5H x 24D in\nBox: 3/4 in plywood\nDoors: Solid wood shaker\nHinges: Soft-close concealed\n"
     "Ships: Fully assembled"),
    ("CAB-SHK-W-W3030", "Shaker White Wall Cabinet 30x30 in – Soft-Close", "Kitchen Cabinets", 4, "piece",
     "30W x 30H x 12D in, assembled", 12900, 21900, 1, 180, [(10, 11900)], 0,
     "Double-door wall cabinet with adjustable shelves, matches the Shaker White collection.",
     "Size: 30W x 30H x 12D in\nShelves: 2 adjustable\nBox: 1/2 in plywood\nShips: Fully assembled"),
    ("CAB-SHK-W-SB36", "Shaker White Sink Base Cabinet 36 in", "Kitchen Cabinets", 4, "piece",
     "36W x 34.5H x 24D in, assembled", 21900, 36900, 1, 90, [(10, 19900)], 0,
     "Sink base with false drawer front and open interior for plumbing.",
     "Size: 36W x 34.5H x 24D in\nBox: 3/4 in plywood\nShips: Fully assembled"),
    ("VAN-36-GRY-QTZ", "36 in Freestanding Vanity – Grey Oak with Quartz Top & Sink", "Bathroom Vanities", 4, "set",
     "Cabinet + quartz top + undermount sink", 38900, 69900, 1, 64, [(5, 36500), (10, 34900)], 1,
     "Complete vanity set: solid-wood cabinet, 1-inch white quartz top with backsplash, and a ceramic undermount sink.",
     "Width: 36 in\nTop: Engineered quartz, 3-hole 8 in widespread\nDrawers: 2 soft-close\nFaucet: Not included"),
    ("VAN-48-WHT-QTZ", "48 in Freestanding Vanity – White with Quartz Top & Sink", "Bathroom Vanities", 4, "set",
     "Cabinet + quartz top + undermount sink", 52900, 89900, 1, 38, [(5, 49900)], 0,
     "Crisp white shaker vanity with generous storage and a white quartz top.",
     "Width: 48 in\nTop: Engineered quartz\nDrawers: 3 soft-close\nFaucet: Not included"),
    ("FAU-KIT-PD-BN", "Pull-Down Kitchen Faucet – Brushed Nickel", "Faucets & Fixtures", 2, "piece",
     "Single handle, 1 or 3 hole install", 6800, 14900, 1, 520, [(10, 6100), (50, 5500)], 1,
     "Solid brass body, ceramic disc cartridge, dual-function spray head with magnetic dock. cUPC certified.",
     "Material: Solid brass\nCartridge: Ceramic disc\nFlow rate: 1.8 GPM\nCertification: cUPC, NSF 61\n"
     "Includes: Deck plate, supply lines"),
    ("FAU-BTH-WS-MB", "8 in Widespread Bathroom Faucet – Matte Black", "Faucets & Fixtures", 2, "piece",
     "3-hole install, pop-up drain included", 5400, 11900, 1, 410, [(10, 4800), (50, 4400)], 0,
     "Two-handle widespread faucet in a durable matte black finish.",
     "Material: Brass\nFlow rate: 1.2 GPM (WaterSense)\nIncludes: Pop-up drain"),
    ("SHW-SYS-12-BN", "12 in Rain Shower System with Handheld – Brushed Nickel", "Faucets & Fixtures", 2, "set",
     "Rough-in valve included", 15900, 32900, 1, 150, [(10, 14500)], 0,
     "Pressure-balance shower system with 12-inch rain head, handheld wand and diverter. Valve included.",
     "Shower head: 12 in square\nValve: Pressure balance, included\nFlow rate: 1.8 GPM"),
    ("LGT-CAN-6-5CCT", "6 in LED Canless Recessed Light, 5CCT, 12W (4-pack)", "Lighting", 3, "4-pack",
     "4 lights per pack", 3999, 6999, 1, 1800, [(10, 3599), (50, 3199)], 1,
     "Ultra-thin wafer lights with selectable color temperature (2700K–5000K). Dimmable, IC-rated, ETL listed.",
     "Wattage: 12W (110W equiv.)\nLumens: 1050\nCCT: 2700/3000/3500/4000/5000K\nDimmable: Yes\n"
     "Listing: ETL, Energy Star"),
    ("LGT-VAN-3L-BLK", "3-Light Bathroom Vanity Fixture – Matte Black", "Lighting", 3, "piece",
     "24 in wide, E26 bulbs (not included)", 5900, 9900, 1, 260, [(10, 5200)], 0,
     "Modern vanity light with frosted glass shades. Mounts up or down.",
     "Width: 24 in\nBulbs: 3 x E26, 60W max\nListing: UL damp location"),
    ("HDW-LEV-PASS-MB", "Passage Door Lever – Matte Black (Keyless)", "Doors & Hardware", 5, "piece",
     "For hallways and closets", 1250, 2400, 1, 2400, [(10, 1090), (50, 950)], 0,
     "Heavy zinc-alloy lever for interior doors. Reversible, adjustable backset. Contractor favorite.",
     "Function: Passage (no lock)\nBackset: Adjustable 2-3/8 or 2-3/4 in\nDoor thickness: 1-3/8 to 1-3/4 in"),
    ("HDW-BARN-6FT", "6.6 ft Sliding Barn Door Hardware Kit – Black Steel", "Doors & Hardware", 5, "set",
     "Fits doors 36–40 in wide", 7900, 14900, 1, 310, [(10, 6900)], 0,
     "Heavy-duty carbon-steel track and quiet nylon wheels, holds up to 220 lb.",
     "Track length: 6.6 ft\nLoad capacity: 220 lb\nDoor thickness: 1-3/8 to 1-3/4 in\nIncludes: All hardware"),
]


def seed_demo(db):
    supplier_ids = [
        db.execute(
            "INSERT INTO suppliers (name, location, commission_rate, portal_token, contact_email) VALUES (?, ?, ?, ?, ?)",
            (name, location, rate, secrets.token_urlsafe(18), "factory@example.com"),
        ).lastrowid
        for name, location, rate in SUPPLIERS
    ]
    for i, (name, description) in enumerate(CATEGORIES):
        cat_id = get_or_create_category(db, name)
        db.execute("UPDATE categories SET description = ?, sort_order = ? WHERE id = ?", (description, i, cat_id))

    for (sku, name, category, sup, unit, unit_note, price, compare_at, moq, stock, tiers, featured,
         description, specs) in PRODUCTS:
        product_id = db.execute(
            """INSERT INTO products (sku, slug, name, category_id, supplier_id, description, specs, unit, unit_note,
                   price_cents, compare_at_cents, moq, warehouse, featured)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (sku, unique_slug(db, "products", slugify(name)), name, get_or_create_category(db, category),
             supplier_ids[sup], description, specs, unit, unit_note, price, compare_at, moq, "Ontario, CA", featured),
        ).lastrowid
        set_tiers(db, product_id, tiers)
        record_movement(db, product_id, stock, "receive", f"Container DEMU{4000000 + product_id * 7919 % 999999:07d}",
                        "Demo shipment")
    db.commit()

    # A few sample orders so dashboards and factory statements have something to show.
    def line(sku, qty):
        from .catalog import get_tiers
        from .pricing import unit_price

        p = db.execute(PRODUCT_SELECT + " WHERE p.sku = ?", (sku,)).fetchone()
        price = unit_price(p["price_cents"], get_tiers(db, p["id"]), qty)
        return {"product": p, "qty": qty, "unit_price_cents": price, "line_total_cents": price * qty}

    customer = {"customer_name": "Demo Contractor LLC", "email": "buyer@example.com", "phone": "555-0100",
                "customer_type": "Contractor / Remodeler", "fulfillment": "pickup"}
    first = orders.place_order(db, customer, [line("SPC-7MM-OAK", 120), line("HDW-LEV-PASS-MB", 60)], 0, "invoice")
    orders.set_status(db, first["id"], "fulfilled")
    second = orders.place_order(
        db, {**customer, "customer_name": "Demo Homeowner", "customer_type": "Homeowner", "fulfillment": "delivery",
             "address": "123 Demo St", "city": "Riverside", "state": "CA", "zip": "92501"},
        [line("VAN-36-GRY-QTZ", 2), line("FAU-BTH-WS-MB", 2), line("TIL-POR-1224-CEM", 12)], 15000, "invoice")
    orders.set_status(db, second["id"], "paid")
    orders.place_order(db, customer, [line("CAB-SHK-W-B30", 6), line("CAB-SHK-W-W3030", 8)], 0, "invoice")

    db.execute("INSERT INTO payouts (supplier_id, amount_cents, reference, note) VALUES (?, ?, ?, ?)",
               (supplier_ids[1], 300000, "Wire #DEMO-001", "Demo partial payout"))
    db.execute(
        "INSERT INTO inquiries (kind, name, email, phone, company, role, location, items, message) "
        "VALUES ('quote', 'Demo Builder', 'builder@example.com', '555-0101', 'Demo Homes Inc', "
        "'Contractor / Remodeler', '92336', 'TIL-POR-2448-CAL × 300 boxes\nVAN-36-GRY-QTZ × 24', "
        "'12-unit townhouse project, need delivery in 3 weeks.')"
    )
    db.commit()

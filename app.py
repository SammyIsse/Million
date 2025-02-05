from flask import Flask, render_template, send_from_directory, jsonify, request, redirect, url_for, render_template_string
import requests
import xmltodict
from datetime import datetime, timedelta
import json
import pandas as pd
import math  # Added for math.isnan

app = Flask(__name__)

# Cache configuration
CACHE_DURATION = timedelta(minutes=30)
XML_URL = "https://cphapp.rema1000.dk/api/v1/products.xml"
cached_data = {
    'timestamp': None,
    'data': None
}

# Add at the top with other app config
app.cached_products = None
app.last_cache_update = None

def format_price(price_str):
    """Format price string to float"""
    if not price_str:
        return 0.0
    try:
        # Remove currency and whitespace
        cleaned = price_str.replace('DKK', '').replace('kr', '').replace(',', '.').strip()
        return float(cleaned)
    except (ValueError, TypeError):
        print(f"Error converting price: {price_str}")
        return 0.0

def validate_xml_structure(xml_dict):
    """Validate the XML data structure"""
    if not isinstance(xml_dict, dict):
        print("Error: XML data is not a dictionary")
        return False
        
    if 'products' not in xml_dict:
        print("Error: No 'products' element in XML")
        return False
        
    if not isinstance(xml_dict['products'], dict):
        print("Error: 'products' is not a dictionary")
        return False
        
    if 'product' not in xml_dict['products']:
        print("Error: No 'product' element in products")
        return False
        
    if not isinstance(xml_dict['products']['product'], list):
        print("Error: 'product' is not a list")
        return False
        
    return True

def fetch_and_parse_xml():
    """Fetch and parse data from both XML and Excel sources"""
    try:
        print("\n=== Starting data fetch and parse ===")
        
        # Initialize empty lists for both sources
        rema_products = []
        bilka_products = []
        
        # 1. Fetch and parse XML data (Rema 1000)
        print("Fetching XML data from:", XML_URL)
        try:
            response = requests.get(XML_URL, timeout=10)
            response.raise_for_status()
            
            print(f"Response status: {response.status_code}")
            print(f"Response content type: {response.headers.get('content-type', 'unknown')}")
            
            # Parse XML to dict
            xml_dict = xmltodict.parse(response.text)
            
            if validate_xml_structure(xml_dict):
                print(f"XML structure validated successfully")
                
                for i, product in enumerate(xml_dict['products']['product']):
                    try:
                        # Extract price and clean it
                        price = format_price(product.get('price', '0 DKK'))
                        sale_price = format_price(product.get('sale_price', '')) or None
                        
                        # Log sale information for debugging
                        if sale_price:
                            print(f"\nFound sale product:")
                            print(f"Title: {product.get('title', '')}")
                            print(f"Regular price: {price}")
                            print(f"Sale price: {sale_price}")
                            print(f"Raw sale_price_effective_date: {product.get('sale_price_effective_date', 'None')}")
                            if product.get('sale_price_effective_date'):
                                dates = product.get('sale_price_effective_date').split('/')
                                print(f"Split dates: {dates}")
                                if len(dates) > 1:
                                    print(f"End date: {dates[1].strip()}")
                        
                        product_dict = {
                            '/product/id': product.get('id', ''),
                            '/product/title': product.get('title', ''),
                            '/product/price': price,
                            '/product/sale_price': sale_price,
                            '/product/description': product.get('description', ''),
                            '/product/brand': product.get('brand', ''),
                            '/product/imageLink': product.get('imageLink', ''),
                            '/product/product_type': product.get('product_type', ''),
                            '/product/sale_price_effective_date': product.get('sale_price_effective_date', ''),
                            '/product/store': 'Rema 1000'  # Add store field
                        }
                        
                        # Log first few products for debugging
                        if i < 3:
                            print(f"\nProduct {i} parsed from Rema 1000:")
                            print(f"Title: {product_dict['/product/title']}")
                            print(f"Brand: {product_dict['/product/brand']}")
                            print(f"Price: {product_dict['/product/price']}")
                        
                        rema_products.append(product_dict)
                        
                    except Exception as e:
                        print(f"Error processing Rema 1000 product {i}: {str(e)}")
                        print("Product data:", json.dumps(product, indent=2))
                        continue
                
                print(f"\nTotal Rema 1000 products parsed: {len(rema_products)}")
            else:
                print("XML validation failed")
                
        except Exception as e:
            print(f"Error fetching Rema 1000 data: {str(e)}")
            import traceback
            traceback.print_exc()
        
        # 2. Read Excel data (Bilka)
        print("\nReading Excel data from Products-bilka.xlsx")
        try:
            print("Attempting to read Excel file...")
            # Skip the first row (index 0) and use second row (index 1) as headers
            df = pd.read_excel('Products-bilka.xlsx', header=1)
            print("Excel file read successfully")
            print(f"Excel columns found: {df.columns.tolist()}")
            print(f"Number of rows in Excel: {len(df)}")
            
            # Add detailed logging for product 90357
            print("\nSearching for product 90357 in Excel data...")
            product_90357 = df[df['/product/id'] == '90357']
            if not product_90357.empty:
                print("Found product 90357 in Excel:")
                print(f"Price: {product_90357['/product/price'].iloc[0]}")
                print(f"Sale Price: {product_90357['/product/sale_price'].iloc[0] if '/product/sale_price' in product_90357.columns else 'N/A'}")
            else:
                print("Product 90357 not found in Excel data")
            
            if len(df) > 0:
                print("\nFirst row of data:")
                print(df.iloc[0].to_dict())
            
            for i, row in df.iterrows():
                try:
                    # Extract price and clean it - using correct column names with /product/ prefix
                    raw_price = str(row['/product/price']) if '/product/price' in df.columns else '0'
                    raw_sale_price = str(row['/product/sale_price']) if '/product/sale_price' in df.columns else ''
                    raw_id = str(row['/product/id']) if '/product/id' in df.columns else '0'

                    print(f"\nProcessing Excel row {i}:")
                    print(f"Raw price: {raw_price}")
                    print(f"Raw sale price: {raw_sale_price}")
                    
                    price = format_price(raw_price) or 0.0
                    sale_price = format_price(raw_sale_price) or None
                    # Add NaN check for sale_price
                    if sale_price is not None and math.isnan(sale_price):
                        sale_price = None
                    
                    print(f"Formatted price: {price}")
                    print(f"Formatted sale price: {sale_price}")
                    print(f"{raw_id}")
                    
                    product_dict = {
                        '/product/id': str(row['/product/id']),
                        '/product/title': str(row['/product/title']),
                        '/product/price': price,
                        '/product/sale_price': sale_price,
                        '/product/description': str(row['/product/description']),
                        '/product/brand': str(row['/product/brand']),
                        '/product/imageLink': str(row['/product/imageLink']),
                        '/product/product_type': str(row['/product/product_type']),
                        '/product/sale_price_effective_date': str(row['/product/sale_price_effective_date']),
                        '/product/store': 'Bilka'
                    }
                    
                    # Skip products with missing or invalid ID
                    if not product_dict['/product/id'] or product_dict['/product/id'] == 'nan':
                        print(f"Skipping row {i} due to missing or invalid ID")
                        continue
                    
                    # Log first few products for debugging
                    if i < 3:
                        print(f"\nProduct {i} parsed from Bilka:")
                        print(f"ID: {product_dict['/product/id']}")
                        print(f"Title: {product_dict['/product/title']}")
                        print(f"Brand: {product_dict['/product/brand']}")
                        print(f"Price: {product_dict['/product/price']}")
                    
                    # TEMPORARY DEBUG FOR PRODUCT 90357
                    if str(product_dict['/product/id']) == '90357':
                        print("\n=== DEBUG BILKA PRODUCT 90357 ===")
                        print("Raw Excel Row Data:")
                        print(row.to_dict())
                        print("Formatted Product Data:")
                        print(json.dumps(product_dict, indent=2))
                        print("Price Type:", type(product_dict['/product/price']))
                        print("Sale Price Type:", type(product_dict['/product/sale_price']))
                    
                    bilka_products.append(product_dict)
                    
                except Exception as e:
                    print(f"Error processing Bilka product {i}:")
                    print(f"Error details: {str(e)}")
                    print("Row data:", row.to_dict())
                continue
                    
            print(f"\nTotal Bilka products parsed: {len(bilka_products)}")
            
        except Exception as e:
            print(f"Error reading Bilka data:")
            print(f"Error type: {type(e).__name__}")
            print(f"Error details: {str(e)}")
            import traceback
            traceback.print_exc()
        
        # 3. Merge and compare products
        print("\nMerging and comparing products from both sources")
        final_products = []
        product_map = {}  # Dictionary to track cheapest products by ID
        
        # Process all products from both sources
        for product in rema_products + bilka_products:
            product_id = product['/product/id']
            current_price = float(product['/product/sale_price']) if (product['/product/sale_price'] is not None and not math.isnan(product['/product/sale_price'])) else float(product['/product/price'])
            current_store = product['/product/store']
            
            # Enhanced debug logging for product 90357
            if str(product_id) == '90357':
                print(f"\n=== Processing Product 90357 ===")
                print(f"Current Store: {current_store}")
                print(f"Raw Price: {product['/product/price']}")
                print(f"Raw Sale Price: {product['/product/sale_price']}")
                print(f"Final Current Price: {current_price}")
            
            if product_id in product_map:
                # Compare with existing product
                existing_price = float(product_map[product_id]['/product/sale_price'] 
                                if product_map[product_id]['/product/sale_price'] 
                                else product_map[product_id]['/product/price'])
                existing_store = product_map[product_id]['/product/store']
                
                # Enhanced debug logging for product 90357
                if str(product_id) == '90357':
                    print(f"Found existing entry:")
                    print(f"Existing Store: {existing_store}")
                    print(f"Existing Raw Price: {product_map[product_id]['/product/price']}")
                    print(f"Existing Raw Sale Price: {product_map[product_id]['/product/sale_price']}")
                    print(f"Final Existing Price: {existing_price}")
                    print(f"Price Comparison: {current_price} <= {existing_price}")
                
                # Keep the cheaper product (using <= to prefer the second store if prices are equal)
                if current_price <= existing_price:
                    if str(product_id) == '90357':
                        print(f"Decision: Keeping product from {current_store}")
                    
                    # Remove old product from final list
                    final_products = [p for p in final_products if p['/product/id'] != product_id]
                    # Add new product
                    final_products.append(product)
                    product_map[product_id] = product
                    product_map[product_id]['/product/store'] = current_store  # Update store reference
                else:
                    if str(product_id) == '90357':
                        print(f"Decision: Keeping existing product from {existing_store}")
            else:
                # New product, add it
                if str(product_id) == '90357':
                    print(f"Adding new product from {current_store}")
                
                final_products.append(product)
                product_map[product_id] = product
                product_map[product_id]['/product/store'] = current_store  # Update store reference
        
        print(f"\nFinal merged product list contains {len(final_products)} products")
        return final_products
        
    except Exception as e:
        print(f"Error in fetch_and_parse_xml: {str(e)}")
        import traceback
        traceback.print_exc()
        return []

def get_product_data():
    """Get product data with caching"""
    global cached_data
    current_time = datetime.now()
    
    # Check if cache is valid
    if (cached_data['timestamp'] is None or 
        cached_data['data'] is None or 
        current_time - cached_data['timestamp'] >= CACHE_DURATION):
        
        # Fetch new data
        products = fetch_and_parse_xml()
        
        # Update cache
        cached_data = {
            'timestamp': current_time,
            'data': products
        }
        
    else:
        print("Using cached data")
    
    return cached_data['data']

@app.route('/')
@app.route('/index.html')
def home():
    # Get product data (either from cache or fresh)
    product_data = get_product_data()
    
    # Create a dictionary to store products by category
    products_by_category = {
         'Ugens Tilbud': [],
        'Kolonial': [],
        'Drikkevarer': [],
        'Mejeri': [],
        'Baby og småbørn': [],
        'Personlig pleje': [],
        'Husholdning': [],
        'Frugt & grønt': [],
        'Nemt & hurtigt': [],
        'Køl': [],
        'Frost': [],
        'Ost m.v.': [],
        'Brød & Bavinchi': [],
        'Kød, fisk & fjerkræ': [],
        'Kiosk': [],
        'Slik': []
    }
    
    # Populate sale products først
    for product in product_data:
        if product['/product/sale_price']:
            try:
                # Get the sale end date
                sale_dates = str(product['/product/sale_price_effective_date']).split('/')
                if len(sale_dates) > 1:
                    try:
                        # Parse the date and reformat to dd/mm
                        date_str = sale_dates[1].strip()
                        print(f"Extracted end date string: {date_str}")
                        date_obj = datetime.strptime(date_str, '%Y-%m-%dT%H:%M:%S%z')
                        sale_end_date = date_obj.strftime('%d/%m')
                        print(f"Formatted sale end date: {sale_end_date}")
                    except ValueError as e:
                        print(f"Error parsing date: {e}")
                        sale_end_date = None
                
                product_dict = {
                    'id': str(product['/product/id']),
                    'name': str(product['/product/title']),
                    'price': float(product['/product/price']),
                    'sale_price': float(product['/product/sale_price']),
                    'description': str(product['/product/description']),
                    'brand': str(product['/product/brand']),
                    'image_url': str(product['/product/imageLink']),
                    'is_sale': True,
                    'sale_end_date': sale_end_date
                }
                products_by_category['Ugens Tilbud'].append(product_dict)
            except (ValueError, TypeError):
                continue

    # Populate regular categories
    for product in product_data:
        category = str(product['/product/product_type'])
        if category in products_by_category:
            try:
                product_dict = {
                    'id': str(product['/product/id']),
                    'name': str(product['/product/title']),
                    'price': float(product['/product/price']),
                    'description': str(product['/product/description']),
                    'brand': str(product['/product/brand']),
                    'image_url': str(product['/product/imageLink']),
                    'is_sale': False
                }
                products_by_category[category].append(product_dict)
            except (ValueError, TypeError):
                continue

    # Begræns til 3 kategorier
    trimmed_categories = {k: v[:6] for k, v in products_by_category.items() if v}

    # Create a mapping for template filenames
    template_mapping = {
        'Ugens Tilbud': 'sale.html',
        'Kolonial': 'Kolonial.html',
        'Drikkevarer': 'Drikkevarer.html',

    }

    return render_template(
        'index.html',
        categories=trimmed_categories,
        template_mapping=template_mapping,
        debug=True  # Add debug flag
    )

@app.route('/sale.html')
def sale():
    try:
        page = request.args.get('page', 1, type=int)
        per_page = 60  # 6x10 layout
        
        product_data = get_product_data()
        sale_products = []
        
        for product in product_data:
            if product['/product/sale_price']:
                try:
                    # Get the sale end date
                    sale_dates = str(product['/product/sale_price_effective_date']).split('/')
                    sale_end_date = None
                    if len(sale_dates) > 1:
                        try:
                            # Parse the date and reformat to dd/mm
                            date_str = sale_dates[1].strip()
                            print(f"Extracted end date string: {date_str}")
                            date_obj = datetime.strptime(date_str, '%Y-%m-%dT%H:%M:%S%z')
                            sale_end_date = date_obj.strftime('%d/%m')
                            print(f"Formatted sale end date: {sale_end_date}")
                        except ValueError as e:
                            print(f"Error parsing date: {e}")
                            sale_end_date = None
                    
                    product_dict = {
                        'id': str(product['/product/id']),
                        'name': str(product['/product/title']),
                        'price': float(product['/product/price']),
                        'sale_price': float(product['/product/sale_price']),
                        'description': str(product['/product/description']),
                        'brand': str(product['/product/brand']),
                        'image_url': str(product['/product/imageLink']),
                        'is_sale': True,
                        'sale_end_date': sale_end_date
                    }
                    sale_products.append(product_dict)
                except (ValueError, TypeError) as e:
                    print(f"Error converting prices for sale product {product['/product/id']} - {product['/product/title']}: {str(e)}")
                    continue
        
        # Calculate pagination
        total_products = len(sale_products)
        total_pages = (total_products + per_page - 1) // per_page
        page = min(max(page, 1), total_pages)  # Ensure page is within valid range
        start_idx = (page - 1) * per_page
        end_idx = start_idx + per_page
        paginated_products = sale_products[start_idx:end_idx]
        
        return render_template('category.html', 
                            category_name='Ugens Tilbud',
                            products=paginated_products,
                            current_page=page,
                            total_pages=total_pages)
                            
    except Exception as e:
        print(f"Error loading sale page: {str(e)}")
        return "Page not found", 404

@app.route('/search')
def search():
    """API endpoint for search suggestions as user types"""
    print("\n=== Starting search request ===")
    query = request.args.get('q', '').lower().strip()
    print(f"Search query: '{query}'")
    
    if not query:
        print("Empty query, returning early")
        return jsonify(html='<div class="no-results">Indtast søgeord</div>')
    
    try:
        product_data = get_product_data()
        print(f"Retrieved {len(product_data)} products from cache/XML")
        
        all_products = []
        match_count = 0
        
        for product in product_data:
            try:
                if not product.get('/product/title') or not product.get('/product/id'):
                    continue
                    
                product_dict = {
                    'id': str(product['/product/id']),
                    'name': str(product['/product/title']),
                    'price': float(product['/product/price']),
                    'description': str(product['/product/description']),
                    'brand': str(product['/product/brand']),
                    'image_url': str(product['/product/imageLink']),
                    'is_sale': False
                }
                
                if product['/product/sale_price']:
                    product_dict['is_sale'] = True
                    product_dict['sale_price'] = float(product['/product/sale_price'])
                    # Add sale end date processing
                    sale_dates = str(product['/product/sale_price_effective_date']).split('/')
                    sale_end_date = None
                    if len(sale_dates) > 1:
                        try:
                            date_str = sale_dates[1].strip()
                            date_obj = datetime.strptime(date_str, '%Y-%m-%dT%H:%M:%S%z')
                            sale_end_date = date_obj.strftime('%d/%m')
                        except ValueError as e:
                            print(f"Error parsing date: {e}")
                            sale_end_date = None
                    product_dict['sale_end_date'] = sale_end_date
                
                # Search in product fields
                product_name = product_dict['name'].lower()
                product_brand = product_dict['brand'].lower()
                product_description = product_dict['description'].lower()
                
                # Log first few products being searched
                if match_count < 3:
                    print(f"\nChecking product:")
                    print(f"Name: {product_name}")
                    print(f"Brand: {product_brand}")
                    print(f"Query: {query}")
                
                # Split query into words and check if any word matches
                search_terms = query.split()
                for term in search_terms:
                    if term in product_name or term in product_brand or term in product_description:
                        all_products.append(product_dict)
                        match_count += 1
                        if match_count <= 3:
                            print(f"Match found! Term '{term}' found in product {product_dict['name']}")
                        break
                    
            except (ValueError, TypeError, KeyError) as e:
                print(f"Error processing product: {str(e)}")
                continue
        
        print(f"\nTotal matches found: {len(all_products)}")
        
        if len(all_products) == 0:
            print("No matches found, returning no results message")
            return jsonify(html='<div class="no-results">Ingen resultater fundet</div>')
            
        # Generate HTML for matched products
        products_html = render_template_string('''
            {% for product in products %}
                <div id="product{{ product.id }}" class="product" onclick="openOverlay('product{{ product.id }}')">
                    <div class="product-image-container">
                        {% if product.is_sale %}
                            <img src="{{ url_for('static', filename='images/Rabat.png') }}" alt="Tilbud" class="sale-badge">
                        {% endif %}
                        <img src="{{ product.image_url }}" alt="Billede-er-på-vej.png" class="product-image">
                    </div>
                    <div class="product-content">
                        <h3>{{ product.name }}</h3>
                        {% if product.is_sale %}
                            <p class="price original">{{ "%.2f"|format(product.price) }} DKK</p>
                            <p class="price sale">{{ "%.2f"|format(product.sale_price) }} DKK</p>
                        {% else %}
                            <p class="price">{{ "%.2f"|format(product.price) }} DKK</p>
                        {% endif %}
                        <p>{{ product.description }}</p>
                        <p class="brand">{{ product.brand }}</p>
                        {% if product.is_sale and product.sale_end_date %}
                            <p class="sale-end-date">Tilbud frem til: {{ product.sale_end_date }}</p>
                        {% endif %}
                    </div>
                    <div class="corner-box" onclick="event.stopPropagation(); addToCart(event, 'product{{ product.id }}')">
                        Tilføj til kurv
                    </div>
                </div>
            {% endfor %}
        ''', products=all_products)
        
        print("Successfully generated HTML for search results")
        return jsonify(html=products_html)
        
    except Exception as e:
        print(f"Error in search route: {str(e)}")
        import traceback
        traceback.print_exc()
        return jsonify(html='<div class="error">Der opstod en fejl under søgningen</div>')

@app.route('/search/results')
def search_page():
    """Full page search results"""
    try:
        page = request.args.get('page', 1, type=int)
        query = request.args.get('q', '').lower().strip()
        per_page = 60  # 6x10 layout
        
        if not query:
            return redirect(url_for('home'))
        
        product_data = get_product_data()
        all_products = []
        
        for product in product_data:
            try:
                if not product.get('/product/title') or not product.get('/product/id'):
                    continue
                    
                product_dict = {
                    'id': str(product['/product/id']),
                    'name': str(product['/product/title']),
                    'price': float(product['/product/price']),
                    'description': str(product['/product/description']),
                    'brand': str(product['/product/brand']),
                    'image_url': str(product['/product/imageLink']),
                    'is_sale': False
                }
                
                if product['/product/sale_price']:
                    product_dict['is_sale'] = True
                    product_dict['sale_price'] = float(product['/product/sale_price'])
                    # Add sale end date processing
                    sale_dates = str(product['/product/sale_price_effective_date']).split('/')
                    sale_end_date = None
                    if len(sale_dates) > 1:
                        try:
                            date_str = sale_dates[1].strip()
                            date_obj = datetime.strptime(date_str, '%Y-%m-%dT%H:%M:%S%z')
                            sale_end_date = date_obj.strftime('%d/%m')
                        except ValueError as e:
                            print(f"Error parsing date: {e}")
                            sale_end_date = None
                    product_dict['sale_end_date'] = sale_end_date
                
                # Search in product fields
                product_name = product_dict['name'].lower()
                product_brand = product_dict['brand'].lower()
                product_description = product_dict['description'].lower()
                
                # Split query into words and check if any word matches
                search_terms = query.split()
                for term in search_terms:
                    if term in product_name or term in product_brand or term in product_description:
                        all_products.append(product_dict)
                        break
                    
            except (ValueError, TypeError, KeyError) as e:
                print(f"Error processing product: {str(e)}")
                continue
        
        # Calculate pagination
        total_products = len(all_products)
        if total_products == 0:
            return render_template('search_results.html', 
                                query=query,
                                products=[],
                                total_products=0,
                                current_page=1,
                                total_pages=1)
            
        total_pages = (total_products + per_page - 1) // per_page
        page = min(max(page, 1), total_pages)
        start_idx = (page - 1) * per_page
        end_idx = start_idx + per_page
        paginated_products = all_products[start_idx:end_idx]

        return render_template('search_results.html',
                            query=query,
                            products=paginated_products,
                            total_products=total_products,
                            current_page=page,
                            total_pages=total_pages)
    
    except Exception as e:
        print(f"Error in search: {str(e)}")
        return render_template('search_results.html',
                            query=query,
                            products=[],
                            total_products=0,
                            current_page=1,
                            total_pages=1,
                            error="Der opstod en fejl under søgningen")

@app.route('/<category_name>.html')
def category(category_name):
    # Reverse mapping for filenames to category names
    category_mapping = {
        'Kolonial': 'Kolonial',
        'Drikkevarer': 'Drikkevarer',
        'Mejeri': 'Mejeri',
        'Frugt_og_groent': 'Frugt & grønt',
        'Nemt_og_hurtigt': 'Nemt & hurtigt',
        'Koel': 'Køl',
        'Frost': 'Frost',
        'Ost_mv': 'Ost m.v.',
        'Broed_og_Bavinchi': 'Brød & Bavinchi',
        'Koed_fisk_og_fjerkrae': 'Kød, fisk & fjerkræ',
        'Slik': 'Slik'
    }
    
    try:
        page = request.args.get('page', 1, type=int)
        per_page = 60  # 6x10 layout
        
        # Get the actual category name from the filename
        actual_category = category_mapping.get(category_name.replace('.html', ''))
        if not actual_category:
            return "Category not found", 404
            
        # Get products for this category
        product_data = get_product_data()
        category_products = []
        
        print("\n=== Processing products for category:", actual_category, "===")
        
        for product in product_data:
            if str(product['/product/product_type']) == actual_category:
                try:
                    # Log raw sale price effective date
                    if product['/product/sale_price']:
                        print(f"\nProcessing sale product:")
                        print(f"Product ID: {product['/product/id']}")
                        print(f"Product Name: {product['/product/title']}")
                        print(f"Raw sale_price_effective_date: {product['/product/sale_price_effective_date']}")
                    
                    # Get the sale end date if it's a sale product
                    sale_end_date = None
                    if product['/product/sale_price']:
                        sale_dates = str(product['/product/sale_price_effective_date']).split('/')
                        print(f"Split sale dates: {sale_dates}")
                        
                        if len(sale_dates) > 1:
                            try:
                                # Parse the date and reformat to dd/mm
                                date_str = sale_dates[1].strip()
                                print(f"Extracted end date string: {date_str}")
                                date_obj = datetime.strptime(date_str, '%Y-%m-%dT%H:%M:%S%z')
                                sale_end_date = date_obj.strftime('%d/%m')
                                print(f"Formatted sale end date: {sale_end_date}")
                            except ValueError as e:
                                print(f"Error parsing date: {e}")
                                sale_end_date = None
                            except ValueError:
                                sale_end_date = None

                    product_dict = {
                        'id': str(product['/product/id']),
                        'name': str(product['/product/title']),
                        'price': float(product['/product/price']),
                        'description': str(product['/product/description']),
                        'brand': str(product['/product/brand']),
                        'image_url': str(product['/product/imageLink']),
                        'is_sale': False,
                        'sale_end_date': sale_end_date
                    }
                    
                    # Check if it's a sale product
                    if product['/product/sale_price']:
                        product_dict['is_sale'] = True
                        product_dict['sale_price'] = float(product['/product/sale_price'])
                        print(f"Final product dict sale info:")
                        print(f"is_sale: {product_dict['is_sale']}")
                        print(f"sale_end_date: {product_dict['sale_end_date']}")
                    
                    category_products.append(product_dict)
                except (ValueError, TypeError) as e:
                    print(f"Error converting price for product {product['/product/id']} - {product['/product/title']}: {str(e)}")
                    continue

        # Calculate pagination
        total_products = len(category_products)
        total_pages = (total_products + per_page - 1) // per_page
        page = min(max(page, 1), total_pages)  # Ensure page is within valid range
        start_idx = (page - 1) * per_page
        end_idx = start_idx + per_page
        paginated_products = category_products[start_idx:end_idx]
        
        return render_template('category.html', 
                            category_name=actual_category,
                            products=paginated_products,
                            current_page=page,
                            total_pages=total_pages)
                            
    except Exception as e:
        print(f"Error loading category page: {str(e)}")
        return "Page not found", 404

@app.route('/static/<path:filename>')
def static_files(filename):
    return send_from_directory('static', filename)

@app.route('/static/images/<path:filename>')
def serve_static_images(filename):
    return send_from_directory('static/images', filename)

@app.route('/product/<product_id>')
def get_product_info(product_id):
    """Get product information and print debug info"""
    try:
        product_data = get_product_data()
        
        # Find the product with the matching ID
        product = next((p for p in product_data if str(p['/product/id']) == str(product_id)), None)
        
        if product:
            # Print debug information
            print("\n=== Product Information Debug ===")
            print("Product ID:", product['/product/id'])
            print("Title:", product['/product/title'])
            print("Price:", product['/product/price'])
            print("Sale Price:", product['/product/sale_price'])
            print("Description:", product['/product/description'])
            print("Brand:", product['/product/brand'])
            print("Product Type:", product['/product/product_type'])
            print("Store:", product['/product/store'])
            print("Image Link:", product['/product/imageLink'])
            if product['/product/sale_price']:
                print("Sale Price Effective Date:", product['/product/sale_price_effective_date'])
            print("================================\n")
            
            return jsonify({
                'success': True,
                'product': {
                    'rema_price': product['/product/price'],
                    'bilka_price': product['/product/price']
                }
            })
        else:
            print(f"Product not found with ID: {product_id}")
            return jsonify(success=False, error="Product not found"), 404
            
    except Exception as e:
        print(f"Error getting product info: {str(e)}")
        return jsonify(success=False, error=str(e)), 500

@app.route('/api/products', methods=['GET'])
def get_separate_products():
    try:
        # Check if cache is valid (1 hour)
        if app.cached_products and app.last_cache_update:
            if datetime.now() - app.last_cache_update < timedelta(hours=1):
                print("Returning cached products")
                return jsonify({
                    'success': True,
                    'rema_products': app.cached_products['rema'],
                    'bilka_products': app.cached_products['bilka']
                })
        
        # Cache is stale or missing, regenerate
        print("Regenerating product cache")
        rema = parse_rema_xml()
        bilka = parse_bilka_excel()
        
        app.cached_products = {
            'rema': rema,
            'bilka': bilka
        }
        app.last_cache_update = datetime.now()
        
        return jsonify({
            'success': True,
            'rema_products': rema,
            'bilka_products': bilka
        })
        
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

def parse_rema_xml():
    try:
        print("\n=== Starting data fetch and parse ===")
        
        # Initialize empty lists for both sources
        rema_products = []

        
        # 1. Fetch and parse XML data (Rema 1000)
        print("Fetching XML data from:", XML_URL)
        try:
            response = requests.get(XML_URL, timeout=10)
            response.raise_for_status()
            
            print(f"Response status: {response.status_code}")
            print(f"Response content type: {response.headers.get('content-type', 'unknown')}")
            
            # Parse XML to dict
            xml_dict = xmltodict.parse(response.text)
            
            if validate_xml_structure(xml_dict):
                print(f"XML structure validated successfully")
                
                for i, product in enumerate(xml_dict['products']['product']):
                    try:
                        # Extract price and clean it
                        price = format_price(product.get('price', '0 DKK'))
                        sale_price = format_price(product.get('sale_price', '')) or None
                        
                        # Log sale information for debugging
                        if sale_price:
                            print(f"\nFound sale product:")
                            print(f"Title: {product.get('title', '')}")
                            print(f"Regular price: {price}")
                            print(f"Sale price: {sale_price}")
                            print(f"Raw sale_price_effective_date: {product.get('sale_price_effective_date', 'None')}")
                            if product.get('sale_price_effective_date'):
                                dates = product.get('sale_price_effective_date').split('/')
                                print(f"Split dates: {dates}")
                                if len(dates) > 1:
                                    print(f"End date: {dates[1].strip()}")
                        
                        product_dict = {
                            '/product/id': product.get('id', ''),
                            '/product/title': product.get('title', ''),
                            '/product/price': price,
                            '/product/sale_price': sale_price,
                            '/product/description': product.get('description', ''),
                            '/product/brand': product.get('brand', ''),
                            '/product/imageLink': product.get('imageLink', ''),
                            '/product/product_type': product.get('product_type', ''),
                            '/product/sale_price_effective_date': product.get('sale_price_effective_date', ''),
                            '/product/store': 'Rema 1000'  # Add store field
                        }
                        
                        # Log first few products for debugging
                        if i < 3:
                            print(f"\nProduct {i} parsed from Rema 1000:")
                            print(f"Title: {product_dict['/product/title']}")
                            print(f"Brand: {product_dict['/product/brand']}")
                            print(f"Price: {product_dict['/product/price']}")
                        
                        rema_products.append(product_dict)
                        
                    except Exception as e:
                        print(f"Error processing Rema 1000 product {i}: {str(e)}")
                        print("Product data:", json.dumps(product, indent=2))
                        continue
                
                print(f"\nTotal Rema 1000 products parsed: {len(rema_products)}")
            else:
                print("XML validation failed")
                
        except Exception as e:
            print(f"Error fetching Rema 1000 data: {str(e)}")
            import traceback
            traceback.print_exc()

        return rema_products
    except Exception as e:
        print(f"Error parsing Rema XML: {str(e)}")
        return []

def parse_bilka_excel():
    try:
        print("\nReading Excel data from Products-bilka.xlsx")
        bilka_products = []
        print("Attempting to read Excel file...")
        # Skip the first row (index 0) and use second row (index 1) as headers
        df = pd.read_excel('Products-bilka.xlsx', header=1)
        print("Excel file read successfully")
        print(f"Excel columns found: {df.columns.tolist()}")
        print(f"Number of rows in Excel: {len(df)}")
        
        # Add detailed logging for product 90357
        print("\nSearching for product 90357 in Excel data...")
        product_90357 = df[df['/product/id'] == '90357']
        if not product_90357.empty:
            print("Found product 90357 in Excel:")
            print(f"Price: {product_90357['/product/price'].iloc[0]}")
            print(f"Sale Price: {product_90357['/product/sale_price'].iloc[0] if '/product/sale_price' in product_90357.columns else 'N/A'}")
        else:
            print("Product 90357 not found in Excel data")
        
        if len(df) > 0:
            print("\nFirst row of data:")
            print(df.iloc[0].to_dict())
        
        for i, row in df.iterrows():
            try:
                # Extract price and clean it - using correct column names with /product/ prefix
                raw_price = str(row['/product/price']) if '/product/price' in df.columns else '0'
                raw_sale_price = str(row['/product/sale_price']) if '/product/sale_price' in df.columns else ''
                raw_id = str(row['/product/id']) if '/product/id' in df.columns else '0'

                print(f"\nProcessing Excel row {i}:")
                print(f"Raw price: {raw_price}")
                print(f"Raw sale price: {raw_sale_price}")
                
                price = format_price(raw_price) or 0.0
                sale_price = format_price(raw_sale_price) or None
                # Add NaN check for sale_price
                if sale_price is not None and math.isnan(sale_price):
                    sale_price = None
                
                print(f"Formatted price: {price}")
                print(f"Formatted sale price: {sale_price}")
                print(f"{raw_id}")
                
                product_dict = {
                    '/product/id': str(row['/product/id']),
                    '/product/title': str(row['/product/title']),
                    '/product/price': price,
                    '/product/sale_price': sale_price,
                    '/product/description': str(row['/product/description']),
                    '/product/brand': str(row['/product/brand']),
                    '/product/imageLink': str(row['/product/imageLink']),
                    '/product/product_type': str(row['/product/product_type']),
                    '/product/sale_price_effective_date': str(row['/product/sale_price_effective_date']),
                    '/product/store': 'Bilka'
                }
                
                # Skip products with missing or invalid ID
                if not product_dict['/product/id'] or product_dict['/product/id'] == 'nan':
                    print(f"Skipping row {i} due to missing or invalid ID")
                    continue
                
                # Log first few products for debugging
                if i < 3:
                    print(f"\nProduct {i} parsed from Bilka:")
                    print(f"ID: {product_dict['/product/id']}")
                    print(f"Title: {product_dict['/product/title']}")
                    print(f"Brand: {product_dict['/product/brand']}")
                    print(f"Price: {product_dict['/product/price']}")
                
                # TEMPORARY DEBUG FOR PRODUCT 90357
                if str(product_dict['/product/id']) == '90357':
                    print("\n=== DEBUG BILKA PRODUCT 90357 ===")
                    print("Raw Excel Row Data:")
                    print(row.to_dict())
                    print("Formatted Product Data:")
                    print(json.dumps(product_dict, indent=2))
                    print("Price Type:", type(product_dict['/product/price']))
                    print("Sale Price Type:", type(product_dict['/product/sale_price']))
                
                bilka_products.append(product_dict)
                
            except Exception as e:
                print(f"Error processing Bilka product {i}:")
                print(f"Error details: {str(e)}")
                print("Row data:", row.to_dict())
            continue
                
        print(f"\nTotal Bilka products parsed: {len(bilka_products)}")

        return bilka_products
    except Exception as e:
        print(f"Error reading Bilka data: {e}")
        return []

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5001)


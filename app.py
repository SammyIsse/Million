from flask import Flask, render_template, send_from_directory, jsonify, request, redirect, url_for
import requests
import xmltodict
from datetime import datetime, timedelta
import json

app = Flask(__name__)

# Cache configuration
CACHE_DURATION = timedelta(minutes=30)
XML_URL = "https://cphapp.rema1000.dk/api/v1/products.xml"
cached_data = {
    'timestamp': None,
    'data': None
}

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
    """Fetch XML data and parse it into the required format"""
    try:
        print("\n=== Starting XML fetch and parse ===")
        print("Fetching XML data from:", XML_URL)
        response = requests.get(XML_URL, timeout=10)  # Add timeout
        response.raise_for_status()  # Raise an exception for bad status codes       
        
        # Parse XML to dict
        xml_dict = xmltodict.parse(response.text)

        # Convert the XML structure to match our needed format
        products = []
        
        for i, product in enumerate(xml_dict['products']['product']):
            try:
                # Extract price and clean it
                price = format_price(product.get('price', '0 DKK'))
                sale_price = format_price(product.get('sale_price', '')) or None
                
                product_dict = {
                    '/product/id': product.get('id', ''),
                    '/product/title': product.get('title', ''),
                    '/product/price': price,
                    '/product/sale_price': sale_price,
                    '/product/description': product.get('description', ''),
                    '/product/brand': product.get('brand', ''),
                    '/product/imageLink': product.get('imageLink', ''),
                    '/product/product_type': product.get('product_type', ''),
                    '/product/sale_price_effective_date': product.get('sale_price_effective_date', '')
                }
                products.append(product_dict)
                
                # Print details of first few products for debugging
            except Exception as e:
                print(f"Error processing product {i}: {str(e)}")
                print("Product data:", json.dumps(product, indent=2))
                continue
        return products
    except requests.exceptions.Timeout:
        print("Error: Request timed out while fetching XML data")
        return []
    except requests.exceptions.ConnectionError:
        print("Error: Could not connect to the XML endpoint")
        return []
    except requests.exceptions.RequestException as e:
        print(f"Error fetching XML data: {str(e)}")
        if 'response' in locals():
            print("Response status code:", response.status_code)
            print("Response content:", response.text[:500])  # Print first 500 chars
        return []
    except Exception as e:
        print(f"Unexpected error: {str(e)}")
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
                product_dict = {
                    'id': str(product['/product/id']),
                    'name': str(product['/product/title']),
                    'price': float(product['/product/price']),
                    'sale_price': float(product['/product/sale_price']),
                    'description': str(product['/product/description']),
                    'brand': str(product['/product/brand']),
                    'image_url': str(product['/product/imageLink']),
                    'is_sale': True
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
                    product_dict = {
                        'id': str(product['/product/id']),
                        'name': str(product['/product/title']),
                        'price': float(product['/product/price']),
                        'sale_price': float(product['/product/sale_price']),
                        'description': str(product['/product/description']),
                        'brand': str(product['/product/brand']),
                        'image_url': str(product['/product/imageLink']),
                        'is_sale': True
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
    try:
        page = request.args.get('page', 1, type=int)
        query = request.args.get('q', '').lower()  # Don't trim here to preserve spaces
        per_page = 60  # 6x10 layout
        
        # Get all products
        product_data = get_product_data()
        all_products = []
                
        for product in product_data:
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
                
                # Check if it's a sale product
                if product['/product/sale_price']:
                    product_dict['is_sale'] = True
                    product_dict['sale_price'] = float(product['/product/sale_price'])
                
                # If there's a search query, filter the products
                if query:
                    product_name = product_dict['name'].lower()
                    product_brand = product_dict['brand'].lower()
                    
                    # Split query into words for better matching
                    search_terms = query.split()
                    matches = True
                    
                    # Check if all search terms are present in name or brand
                    for term in search_terms:
                        if not (term in product_name or term in product_brand):
                            matches = False
                            break
                    
                    if matches:
                        all_products.append(product_dict)
                else:
                    all_products.append(product_dict)
                
            except (ValueError, TypeError) as e:
                print(f"Error converting product data: {str(e)}")
                print(f"Problem product: ID={product.get('/product/id', 'unknown')}, Title={product.get('/product/title', 'unknown')}")
                continue
        
        
        # Calculate pagination
        total_products = len(all_products)
        total_pages = (total_products + per_page - 1) // per_page
        page = min(max(page, 1), total_pages) if total_pages > 0 else 1
        start_idx = (page - 1) * per_page
        end_idx = start_idx + per_page
        paginated_products = all_products[start_idx:end_idx]
                
        return render_template('search.html',
                            category_name='Søgeresultater',
                            products=paginated_products,
                            current_page=page,
                            total_pages=total_pages,
                            search_query=query)
                            
    except Exception as e:
        print(f"Error in search: {str(e)}")
        return "Error performing search", 500

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
        
        for product in product_data:
            if str(product['/product/product_type']) == actual_category:
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
                    
                    # Check if it's a sale product
                    if product['/product/sale_price']:
                        product_dict['is_sale'] = True
                        product_dict['sale_price'] = float(product['/product/sale_price'])
                    
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

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5001)


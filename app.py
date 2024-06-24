import os
import logging
from flask import Flask, render_template, request, jsonify
from flask_cors import CORS
from dotenv import load_dotenv
import requests
import openai
from openai import AzureOpenAI
from datetime import datetime
import re
# Load environment variables
load_dotenv(override=True)

# Create AzureOpenAI client
client = AzureOpenAI(
    azure_endpoint=os.getenv("AZURE_OPENAI_ENDPOINT", ""),
    api_key=os.getenv("AZURE_OPENAI_KEY", ""),
    api_version="2024-02-15-preview"
)

# Set up logging
logging.basicConfig(level=logging.INFO)

# Validate and fetch environment variables
azure_endpoint = os.getenv("AZURE_OPENAI_ENDPOINT")
api_key = os.getenv("AZURE_OPENAI_KEY")
deployment_name = os.getenv("AZURE_OPENAI_DEPLOYMENT_NAME")

if not azure_endpoint or not api_key or not deployment_name:
    raise ValueError("AZURE_OPENAI_ENDPOINT, AZURE_OPENAI_KEY, and AZURE_OPENAI_DEPLOYMENT_NAME must be set as environment variables.")

openai.api_type = "azure"
openai.api_base = azure_endpoint
openai.api_version = "2024-02-15-preview"
openai.api_key = api_key

# Amadeus API setup
amadeus_api_key = os.getenv("AMADEUS_API_KEY")
amadeus_api_secret = os.getenv("AMADEUS_API_SECRET")

# Exchange rate API setup
exchange_api_key = os.getenv("EXCHANGE_API_KEY")

# Create Flask app
app = Flask(__name__)
CORS(app)  # Enable CORS for all routes

# Function to get Amadeus API token
def get_amadeus_token():
    url = "https://test.api.amadeus.com/v1/security/oauth2/token"
    headers = {"Content-Type": "application/x-www-form-urlencoded"}
    data = {
        "grant_type": "client_credentials",
        "client_id": amadeus_api_key,
        "client_secret": amadeus_api_secret
    }
    response = requests.post(url, headers=headers, data=data)
    if response.status_code == 200:
        return response.json()["access_token"]
    else:
        return None

# Function to query Azure OpenAI for flight details extraction
def extract_flight_details(prompt):
    message_history = [
        {"role": "system", "content": "You are a helpful travel assistant."},
        {"role": "user", "content": prompt}
    ]
    
    response = client.chat.completions.create(
        model="gpt35turbo16k",
        messages=message_history,
        temperature=0.7,
        max_tokens=150,
        top_p=0.95,
        frequency_penalty=0,
        presence_penalty=0,
        stop=None
    )
    
    if response:
        return response.choices[0].message.content.strip()
    else:
        return None


# Function to parse extracted flight details
def parse_flight_details(input_text):
    prompt = f"Extract the source city, destination city, and date from this message: '{input_text}' in the format 'source_city: X, destination_city: Y, date: dd/mm/yy'."
    result = extract_flight_details(prompt)
    
    logging.info(f"Extracted details: {result}")
    
    pattern = r"source_city: (\w+(?:\s\w+)*), destination_city: (\w+(?:\s\w+)*), date: (\d{2}/\d{2}/\d{2})"
    match = re.search(pattern, result, re.IGNORECASE)
    if match:
        source_city = match.group(1)
        destination_city = match.group(2)
        date_str = match.group(3)
        
        try:
            date = datetime.strptime(date_str, "%d/%m/%y")
        except ValueError as e:
            logging.error(f"Date parsing error: {e}")
            return None, None, None
        
        return source_city, destination_city, date
    else:
        logging.error("Pattern did not match the extracted details.")
        return None, None, None

# Rest of the code remains the same


# Function to fetch flight data from Amadeus API
def get_flight_data(source, destination, date):
    token = get_amadeus_token()
    if token:
        url = f"https://test.api.amadeus.com/v2/shopping/flight-offers?originLocationCode={source}&destinationLocationCode={destination}&departureDate={date}&adults=1"
        headers = {"Authorization": f"Bearer {token}"}
        response = requests.get(url, headers=headers)
        if response.status_code == 200:
            return response.json()
        else:
            return None
    else:
        return None

# Function to fetch airport code from city name
def get_airport_code(city_name):
    token = get_amadeus_token()
    if token:
        url = f"https://test.api.amadeus.com/v1/reference-data/locations?subType=AIRPORT&keyword={city_name}"
        headers = {"Authorization": f"Bearer {token}"}
        response = requests.get(url, headers=headers)
        if response.status_code == 200:
            data = response.json()
            if "data" in data and len(data["data"]) > 0:
                return data["data"][0]["iataCode"]
            else:
                return None
        else:
            return None
    else:
        return None

# Function to fetch airline names
def get_airline_name(airline_code):
    token = get_amadeus_token()
    if token:
        url = f"https://test.api.amadeus.com/v1/reference-data/airlines?airlineCodes={airline_code}"
        headers = {"Authorization": f"Bearer {token}"}
        response = requests.get(url, headers=headers)
        if response.status_code == 200:
            data = response.json()
            if "data" in data and len(data["data"]) > 0:
                return data["data"][0]["commonName"]
            else:
                return airline_code
        else:
            return airline_code
    else:
        return airline_code

# Function to convert currency to INR
def convert_to_inr(amount, currency):
    if currency == "INR":
        return amount
    url = f"https://api.exchangerate-api.com/v4/latest/{currency}"
    response = requests.get(url)
    if response.status_code == 200:
        rates = response.json().get('rates')
        if rates and "INR" in rates:
            inr_amount = amount * rates["INR"]
            return round(inr_amount, 2)
    return None

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/query', methods=['POST'])
def query():
    user_query = request.form['user_query']
    response_data = {'response': ''}
    
    if "flight" in user_query.lower():
        source_city, destination_city, date = parse_flight_details(user_query)
        
        if source_city and destination_city and date:
            source_code = get_airport_code(source_city)
            destination_code = get_airport_code(destination_city)
            if source_code and destination_code:
                flight_data = get_flight_data(source_code, destination_code, date.strftime("%Y-%m-%d"))
                if flight_data and "data" in flight_data:
                    flights = []
                    for flight in flight_data["data"]:
                        airline_code = flight["itineraries"][0]["segments"][0]["carrierCode"]
                        airline_name = get_airline_name(airline_code)
                        departure = flight["itineraries"][0]["segments"][0]["departure"]["at"]
                        arrival = flight["itineraries"][0]["segments"][0]["arrival"]["at"]
                        duration = flight["itineraries"][0]["duration"]
                        price = float(flight["price"]["total"])
                        currency = flight["price"]["currency"]
                        price_inr = convert_to_inr(price, currency)
                        
                        # Determine tags
                        tag = "Cheapest" if flight["price"]["total"] == min(f["price"]["total"] for f in flight_data["data"]) else "Popular"
                        
                        flights.append({
                            'airline_name': airline_name,
                            'departure': departure,
                            'arrival': arrival,
                            'duration': duration,
                            'price': price_inr if price_inr else price,
                            'currency': currency,
                            'tag': tag
                        })
                    return jsonify({'flights': flights, 'source_city': source_city, 'destination_city': destination_city, 'date': date.strftime('%d/%m/%Y')})
                else:
                    return jsonify({'error': "No flights found or error fetching flight data."})
            else:
                return jsonify({'error': "Error fetching airport codes. Please check the city names."})
        else:
            return jsonify({'error': "Please enter travel details in the format: 'flight from source_city to destination_city on dd/mm/yy'."})
    else:
        response_data['response'] = extract_flight_details(user_query)
    
    return jsonify(response_data)

if __name__ == '__main__':
    app.run(debug=False)  # Debug mode should be False in production

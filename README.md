<?php
// get-info.php
// Usage: GET /get-info.php?nid=...&dob=YYYY-MM-DD&otp=1234
header('Content-Type: application/json; charset=utf-8');

function respond($data, $code = 200) {
    http_response_code($code);
    echo json_encode($data, JSON_UNESCAPED_UNICODE|JSON_PRETTY_PRINT);
    exit;
}

try {
    $nid = isset($_GET['nid']) ? trim($_GET['nid']) : '';
    $dob = isset($_GET['dob']) ? trim($_GET['dob']) : '';
    $otp = isset($_GET['otp']) ? trim($_GET['otp']) : '';

    if (!$nid || !$dob) {
        respond(['error' => 'NID এবং DOB প্রয়োজন। (nid, dob)'], 400);
    }

    // আপনি ব্রুট-ফোর্স করতে বলেননি — তাই OTP অবশ্যই প্রদান করতে হবে
    if (!$otp) {
        respond(['error' => 'OTP প্রদান করুন (otp) — অননুমোদিত ব্রুটফোর্স করা যাবে না।'], 400);
    }

    // ========== CONFIG ==========
    $base = "https://fsmms.dgf.gov.bd";
    $init_url = $base . "/bn/step2/movementContractor";
    $otp_url = $base . "/bn/step2/movementContractor/mov-otp-step";
    $form_url = $base . "/bn/step2/movementContractor/form";

    // cookie file (temp)
    $cookie_file = sys_get_temp_dir() . '/fsmms_' . bin2hex(random_bytes(8)) . '.cookie';

    // Helper: perform cURL
    function curl_request($url, $post = null, $cookie_file = null, $headers = [], $follow = true, $referer = null) {
        $ch = curl_init();
        curl_setopt($ch, CURLOPT_URL, $url);
        curl_setopt($ch, CURLOPT_RETURNTRANSFER, true);
        curl_setopt($ch, CURLOPT_HEADER, true); // we'll separate headers+body
        curl_setopt($ch, CURLOPT_FOLLOWLOCATION, false);
        curl_setopt($ch, CURLOPT_SSL_VERIFYPEER, true);
        curl_setopt($ch, CURLOPT_SSL_VERIFYHOST, 2);
        curl_setopt($ch, CURLOPT_HTTPHEADER, $headers);
        curl_setopt($ch, CURLOPT_USERAGENT, 'Mozilla/5.0 (compatible; PHP script)');
        if ($cookie_file) {
            curl_setopt($ch, CURLOPT_COOKIEJAR, $cookie_file);
            curl_setopt($ch, CURLOPT_COOKIEFILE, $cookie_file);
        }
        if ($referer) curl_setopt($ch, CURLOPT_REFERER, $referer);
        if ($post !== null) {
            curl_setopt($ch, CURLOPT_POST, true);
            curl_setopt($ch, CURLOPT_POSTFIELDS, $post);
        }
        $resp = curl_exec($ch);
        if ($resp === false) {
            $err = curl_error($ch);
            curl_close($ch);
            throw new Exception("cURL error: " . $err);
        }
        $info = curl_getinfo($ch);
        curl_close($ch);

        $header_len = $info['header_size'];
        $raw_headers = substr($resp, 0, $header_len);
        $body = substr($resp, $header_len);
        return ['headers' => $raw_headers, 'body' => $body, 'info' => $info];
    }

    // 1) Initial POST to get session & redirect to mov-verification (like get_cookie)
    $password = '#' . chr(rand(65,90)) . str_pad((string)rand(0,99), 2, '0', STR_PAD_LEFT);
    // Generate random mobile similar to original (but can be any valid-looking number)
    $mobile = '017' . str_pad((string)rand(0,99999999), 8, '0', STR_PAD_LEFT);

    $post_data = http_build_query([
        'nidNumber' => $nid,
        'email' => '',
        'mobileNo' => $mobile,
        'dateOfBirth' => $dob,
        'password' => $password,
        'confirm_password' => $password,
        'next1' => ''
    ]);

    $init_resp = curl_request($init_url, $post_data, $cookie_file, [
        'Content-Type: application/x-www-form-urlencoded'
    ]);

    // Check redirect location header (we expect 302 to mov-verification)
    // parse headers to find Location
    preg_match('/^Location:\s*(.*)$/mi', $init_resp['headers'], $mloc);
    $location = isset($mloc[1]) ? trim($mloc[1]) : null;

    if (!$location || strpos($location, 'mov-verification') === false) {
        // Not redirected to mov-verification -> cannot proceed
        respond(['error' => 'Session initialization failed বা mov-verification এ redirect হয়নি।', 'debug_location' => $location, 'headers' => $init_resp['headers']], 500);
    }

    // 2) Submit OTP (user-provided) — legit flow
    if (!preg_match('/^\d{4}$/', $otp)) {
        respond(['error' => 'OTP ৪ সংখ্যার হতে হবে।'], 400);
    }
    $otp_data = http_build_query([
        'otpDigit1' => $otp[0],
        'otpDigit2' => $otp[1],
        'otpDigit3' => $otp[2],
        'otpDigit4' => $otp[3]
    ]);

    $otp_resp = curl_request($otp_url, $otp_data, $cookie_file, [
        'Content-Type: application/x-www-form-urlencoded'
    ], true, $init_url);

    // After OTP, server may redirect to the form
    preg_match('/^Location:\s*(.*)$/mi', $otp_resp['headers'], $m2);
    $loc2 = isset($m2[1]) ? trim($m2[1]) : null;

    // Accept both direct 302 redirect or direct content showing form
    $ok_to_fetch_form = false;
    if ($loc2 && strpos($loc2, '/step2/movementContractor/form') !== false) {
        $ok_to_fetch_form = true;
    } else {
        // maybe server returned form in body directly. We'll try fetching form anyway.
        // To be robust, check status code:
        $status = $otp_resp['info']['http_code'] ?? 0;
        if ($status == 200) {
            // body might already contain form
            $form_html = $otp_resp['body'];
            $ok_to_fetch_form = true;
        }
    }

    if (!$ok_to_fetch_form) {
        // try GET the form url anyway using current cookie
        $get_form_resp = curl_request($form_url, null, $cookie_file, [], true, $otp_url);
        if ($get_form_resp['info']['http_code'] != 200) {
            respond(['error' => 'ভেরিফিকেশন সফল হলো না অথবা ফর্ম পাওয়া যায়নি।', 'http_code' => $get_form_resp['info']['http_code'], 'headers' => $get_form_resp['headers']], 403);
        }
        $form_html = $get_form_resp['body'];
    } elseif (!isset($form_html)) {
        // If earlier redirect indicated form, GET it now
        $get_form_resp = curl_request($form_url, null, $cookie_file, [], true, $otp_url);
        if ($get_form_resp['info']['http_code'] != 200) {
            respond(['error' => 'ফর্ম পাওয়া যায়নি।', 'http_code' => $get_form_resp['info']['http_code']], 500);
        }
        $form_html = $get_form_resp['body'];
    }

    // 3) Parse form HTML and extract fields by id
    libxml_use_internal_errors(true);
    $dom = new DOMDocument();
    $loaded = $dom->loadHTML('<?xml encoding="utf-8" ?>' . $form_html);
    if (!$loaded) {
        // fallback: return raw HTML for debugging
        respond(['error' => 'ফর্ম পার্সিং করতে সমস্যা।', 'raw_html_snippet' => substr(strip_tags($form_html),0,500)], 500);
    }
    $xpath = new DOMXPath($dom);

    $ids = ["contractorName","fatherName","motherName","spouseName","nidPerDivision",
           "nidPerDistrict","nidPerUpazila","nidPerUnion","nidPerVillage","nidPerWard",
           "nidPerZipCode","nidPerPostOffice", "nidPerHolding","nidPerMouza"];
    $result = [];
    foreach ($ids as $id) {
        $nodes = $xpath->query("//input[@id='$id' or @name='$id']");
        if ($nodes && $nodes->length > 0) {
            $val = $nodes->item(0)->getAttribute('value');
            $result[$id] = $val;
        } else {
            // try select/textarea
            $nodes2 = $xpath->query("//*[@id='$id' or @name='$id']");
            if ($nodes2 && $nodes2->length > 0) {
                $val = $nodes2->item(0)->nodeValue;
                $result[$id] = trim($val);
            } else {
                $result[$id] = "";
            }
        }
    }

    // 4) Map/enrich data (same structure as original)
    $mapped = [
        "nameBangla" => $result['contractorName'] ?? '',
        "nameEnglish" => "",
        "nationalId" => $nid,
        "dateOfBirth" => $dob,
        "fatherName" => $result['fatherName'] ?? '',
        "motherName" => $result['motherName'] ?? '',
        "spouseName" => $result['spouseName'] ?? '',
        "gender" => "",
        "religion" => "",
        "birthPlace" => $result['nidPerDistrict'] ?? '',
        "nationality" => $result['nationality'] ?? '',
        "division" => $result['nidPerDivision'] ?? '',
        "district" => $result['nidPerDistrict'] ?? '',
        "upazila" => $result['nidPerUpazila'] ?? '',
        "union" => $result['nidPerUnion'] ?? '',
        "village" => $result['nidPerVillage'] ?? '',
        "ward" => $result['nidPerWard'] ?? '',
        "zip_code" => $result['nidPerZipCode'] ?? '',
        "post_office" => $result['nidPerPostOffice'] ?? ''
    ];

    // build address line similar to Flask version
    $address_parts = [];
    $address_parts[] = "বাসা/হোল্ডিং: " . (!empty($result['nidPerHolding']) ? $result['nidPerHolding'] : '-');
    if (!empty($result['nidPerVillage'])) $address_parts[] = "গ্রাম/রাস্তা: " . $result['nidPerVillage'];
    if (!empty($result['nidPerMouza'])) $address_parts[] = "মৌজা/মহল্লা: " . $result['nidPerMouza'];
    if (!empty($result['nidPerUnion'])) $address_parts[] = "ইউনিয়ন ওয়ার্ড: " . $result['nidPerUnion'];
    if (!empty($result['nidPerPostOffice']) || !empty($result['nidPerZipCode'])) $address_parts[] = "ডাকঘর: " . ($result['nidPerPostOffice'] ?? '') . " - " . ($result['nidPerZipCode'] ?? '');
    if (!empty($result['nidPerUpazila'])) $address_parts[] = "উপজেলা: " . $result['nidPerUpazila'];
    if (!empty($result['nidPerDistrict'])) $address_parts[] = "জেলা: " . $result['nidPerDistrict'];
    if (!empty($result['nidPerDivision'])) $address_parts[] = "বিভাগ: " . $result['nidPerDivision'];

    $address_line = implode(", ", $address_parts);

    $mapped['permanentAddress'] = $address_line;
    $mapped['presentAddress'] = $address_line;

    // Cleanup cookie file
    if (file_exists($cookie_file)) @unlink($cookie_file);

    respond($mapped, 200);

} catch (Exception $e) {
    respond(['error' => $e->getMessage()], 500);
}

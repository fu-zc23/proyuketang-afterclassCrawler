import os
import re
import sys
import json
import time
import argparse
import requests
from fpdf import FPDF
from PIL import Image
from io import BytesIO
from concurrent.futures import ThreadPoolExecutor


def download_video(video_url, file_path, sessionid, index, total):
    session = requests.Session()
    session.trust_env = False
    session.headers.update({
        "cookie": f"sessionid={sessionid}",
        "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36"
    })
    print(f"Downloading video {index}/{total} ...")
    try:
        response = session.get(video_url, stream=True, timeout=30)
    except requests.exceptions.RequestException as e:
        print(f"Failed to get video {index}/{total}. Error: {e}.")
        return
    if response.status_code not in [200, 206]:
        print(f"Failed to get video {index}/{total}. Status code: {response.status_code}.")
        return
    with open(file_path, "wb") as f:
        for chunk in response.iter_content(chunk_size=1024 * 1024):
            if chunk:
                f.write(chunk)
    print(f"Finished downloading video {index}/{total}.")


def login_and_get_sessionid(session):
    # 1. 获取 csrftoken
    url = "https://pro.yuketang.cn/v/course_meta/user_info"
    response = session.get(url)
    csrftoken = response.cookies.get("csrftoken")
    if not csrftoken:
        print("Error: failed to get csrftoken.")
        sys.exit()
    session.headers.update({
        "cookie": f"csrftoken={csrftoken}"
    })

    # 2. 获取微信登录参数
    url = "https://pro.yuketang.cn/api/v3/user/login/wechat-auth-param"
    response = session.post(url)
    data = response.json()["data"]
    appId = data["appId"]
    state = data["state"]
    redirectUri = data["redirectUri"]

    # 3. 获取 uuid
    params = {
        "appid": appId,
        "scope": "snsapi_login",
        "state": state,
        "redirect_uri": redirectUri
    }
    url = "https://open.weixin.qq.com/connect/qrconnect"
    response = session.get(url, params=params)
    uuid = re.search(r"uuid=([a-zA-Z0-9]+)", response.text).group(1)

    # 4. 获取二维码并弹窗显示
    url = f"https://open.weixin.qq.com/connect/qrcode/{uuid}"
    response = session.get(url)
    img = Image.open(BytesIO(response.content))
    img.show()
    print("请扫码登录...")

    # 5. 轮询扫码状态
    login_url = "https://lp.open.weixin.qq.com/connect/l/qrconnect"
    code = None
    while True:
        params = {
            "uuid": uuid,
            "_": int(time.time() * 1000)
        }
        response = session.get(login_url, params=params)
        text = response.text
        if "wx_errcode=405" in text:
            code = re.search(r"wx_code='(.*?)'", text).group(1)
            print("扫码成功")
            break
        elif "wx_errcode=404" in text:
            print("已扫码，等待确认...")
        elif "wx_errcode=408" in text:
            print("等待扫码...")
        time.sleep(1)

    # 6. 登录平台
    params = {
        "code": code,
        "state": state
    }
    session.get(redirectUri, params=params)

    # 7. 获取 sessionid
    sessionid = session.cookies.get("sessionid")
    if not sessionid:
        print("Error: login failed.")
        sys.exit()
    print(f"登录成功，sessionid: {sessionid}")
    return sessionid


def is_sessionid_valid(session):
    url = "https://pro.yuketang.cn/api/v3/user/basic-info"
    try:
        response = session.get(url)
    except requests.exceptions.RequestException:
        return False
    if response.status_code != 200:
        return False
    try:
        response = response.json()
    except:
        return False
    return response["code"] == 0


def save_config(config):
    with open("config.json", "w") as f:
        json.dump(config, f, ensure_ascii=False, indent=4)


if __name__ == "__main__":

    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--mode",
        choices=["slides", "videos", "both"],
        default="slides",
        help="slides: 下载课件；videos: 下载回放视频；both: 两者都下载"
    )
    args = parser.parse_args()


    # load config
    try:
        with open("config.json", "r") as f:
            config = json.load(f)
            sessionid = config["sessionid"]
            lesson_id = config["lesson_id"]
    except:
        print("Error: config.json not found or invalid.")
        sys.exit()


    # login
    session = requests.Session()
    session.trust_env = False
    session.headers.update({
        "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36"
    })

    # 如果 config 中没有 sessionid，则自动扫码登录
    if not sessionid:
        sessionid = login_and_get_sessionid(session)
        config["sessionid"] = sessionid
        save_config(config)

    session.headers.update({
        "cookie": f"sessionid={sessionid}"
    })

    # 如果 sessionid 过期，则自动重新登录
    if not is_sessionid_valid(session):
        print("sessionid 已失效，请重新扫码登录...")
        sessionid = login_and_get_sessionid(session)
        config["sessionid"] = sessionid
        save_config(config)
        session.headers.update({
            "cookie": f"sessionid={sessionid}"
        })


    # download slides
    if args.mode in ["slides", "both"]:
        num = 1
        url = "https://pro.yuketang.cn/api/v3/lesson-summary/student"

        response = session.get(f"{url}?lesson_id={lesson_id}")
        if response.status_code != 200:
            print("Error:", response.status_code)
            sys.exit()
        response = response.json()
        if response["code"] != 0:
            print(response["message"])
            sys.exit()

        presentations = response["data"]["presentations"]
        lesson_title = response["data"]["lesson"]["title"]
        print(f"Lesson title: {lesson_title}.")
        lesson_title_safe = re.sub(r"[\\/:*?\"<>|]", ".", lesson_title)
        os.makedirs(lesson_title_safe, exist_ok=True)

        total = len(presentations)
        for presentation in presentations:
            presentation_id = presentation["id"]
            presentation_title = presentation["title"]
            response = session.get(
                f"{url}/presentation?presentation_id={presentation_id}&lesson_id={lesson_id}"
            )
            print(f"Downloading presentation {num}/{total}: {presentation_title}...")
            if response.status_code != 200:
                print("Error:", response.status_code)
                sys.exit()
            response = response.json()
            if response["code"] != 0:
                print(response["message"])
                sys.exit()

            pdf = FPDF()
            slides = response["data"]["slides"]
            for slide in slides:
                response = session.get(slide["cover"])
                if response.status_code != 200:
                    print("Error:", response.status_code)
                    sys.exit()
                img = Image.open(BytesIO(response.content))
                width, height = img.size
                pdf.add_page(format=(width * 25.4 / 72, height * 25.4 / 72))
                pdf.image(BytesIO(response.content), x=0, y=0, w=width * 25.4 / 72, h=height * 25.4 / 72)

            presentation_title = re.sub(r"[\\/:*?\"<>|]", ".", presentation_title)
            pdf.output(f"{lesson_title_safe}/{num}_{presentation_title}.pdf")
            print(f"Finished downloading presentation {num}/{total}.")
            num += 1


    # download videos
    if args.mode in ["videos", "both"]:
        replay_url = f"https://pro.yuketang.cn/api/v3/lesson-summary/replay?lesson_id={lesson_id}"
        response = session.get(replay_url)
        if response.status_code != 200:
            print("Error:", response.status_code)
            sys.exit()
        response = response.json()
        if response["code"] != 0:
            print(response["message"])
            sys.exit()

        lesson_title = response["data"].get("lesson", {}).get("title", f"lesson_{lesson_id}")
        lesson_title_safe = re.sub(r"[\\/:*?\"<>|]", ".", lesson_title)
        os.makedirs(lesson_title_safe, exist_ok=True)

        urls = []
        for live in response["data"]["live"]:
            urls.append(live["url"])

        tasks = []
        cnt = 0
        for video_url in urls:
            cnt += 1
            file_path = f"{lesson_title_safe}/{lesson_title_safe}_{cnt}.mp4"
            tasks.append((video_url, file_path, sessionid, cnt, len(urls)))

        with ThreadPoolExecutor(max_workers=4) as executor:
            for task in tasks:
                executor.submit(download_video, *task)

    print("Done.")
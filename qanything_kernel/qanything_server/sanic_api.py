import sys
import os
import time

# 获取当前脚本的绝对路径
current_script_path = os.path.abspath(__file__)

# 获取当前脚本的父目录的路径，即`qanything_server`目录
current_dir = os.path.dirname(current_script_path)

# 获取`qanything_server`目录的父目录，即`qanything_kernel`
parent_dir = os.path.dirname(current_dir)

# 获取根目录：`qanything_kernel`的父目录
root_dir = os.path.dirname(parent_dir)

# 将项目根目录添加到sys.path
sys.path.append(root_dir)

from qanything_kernel.configs.model_config import MILVUS_LITE_LOCATION, VW_3B_MODEL_PATH, VW_7B_MODEL_PATH, VW_3B_MODEL, VW_7B_MODEL
import qanything_kernel.configs.model_config as model_config
from qanything_kernel.utils.custom_log import debug_logger
from qanything_kernel.utils.general_utils import download_file, get_gpu_memory_utilization, check_onnx_version
import torch
import platform

os_system = platform.system()

if os_system != "Darwin":
    cuda_version = torch.version.cuda
    if cuda_version is None:
        raise ValueError("CUDA is not installed.")
    elif float(cuda_version) < 12:
        raise ValueError("CUDA version must be 12.0 or higher.")

python_version = platform.python_version()
python3_version = python_version.split('.')[1]

system_name = None
if os_system == "Windows":
    system_name = 'win_amd64'
elif os_system == "Linux":
    system_name = 'manylinux_2_28_x86_64'
elif os_system == "Darwin":
    os.system(f"pip install onnxruntime==1.17.1")
    os.system(f'CMAKE_ARGS="-DLLAMA_METAL=on" pip install llama-cpp-python')
if system_name is not None:
    if not check_onnx_version("1.17.1"):
        download_url = f"https://aiinfra.pkgs.visualstudio.com/PublicPackages/_apis/packaging/feeds/9387c3aa-d9ad-4513-968c-383f6f7f53b8/pypi/packages/onnxruntime-gpu/versions/1.17.1/onnxruntime_gpu-1.17.1-cp3{python3_version}-cp3{python3_version}-{system_name}.whl/content"
        debug_logger.info(f'开始从{download_url}下载onnxruntime，也可以手动下载并通过pip install *.whl安装')
        whl_name = f'onnxruntime_gpu-1.17.1-cp3{python3_version}-cp3{python3_version}-{system_name}.whl'
        download_file(download_url, whl_name)
        os.system(f"pip install {whl_name}")
else:
    pass
    # raise ValueError(f"Unsupported system: {os_system}")

from milvus import default_server
from .handler import *
from qanything_kernel.core.local_doc_qa import LocalDocQA
from sanic import Sanic
from sanic import response as sanic_response
from argparse import ArgumentParser, Action
from sanic.worker.manager import WorkerManager
import signal
# from vllm.engine.arg_utils import AsyncEngineArgs
import requests
from modelscope import snapshot_download
import subprocess

parser = ArgumentParser()
# parser = AsyncEngineArgs.add_cli_args(parser)
parser.add_argument('--host', dest='host', default='0.0.0.0', help='set host for qanything server')
parser.add_argument('--port', dest='port', default=8777, type=int, help='set port for qanything server')
#  必填参数
parser.add_argument('--model_size', dest='model_size', default=
'3B', help='set LLM model size for qanything server')
parser.add_argument('--device_id', dest='device_id', default=
'0', help='cuda device id for qanything server')
args = parser.parse_args()

model_config.CUDA_DEVICE = args.device_id
os.environ["CUDA_VISIBLE_DEVICES"] = args.device_id

model_size = args.model_size
model_id = None
args.gpu_memory_utilization = get_gpu_memory_utilization(model_size, args.device_id)
debug_logger.info(f"GPU memory utilization: {args.gpu_memory_utilization}")
if model_size == '3B':
    args.model = VW_3B_MODEL_PATH
    model_id = VW_3B_MODEL
elif model_size == '7B':
    args.model = VW_7B_MODEL_PATH
    model_id = VW_7B_MODEL
else:
    raise ValueError(f"Unsupported model size: {model_size}, supported model size: 3B, 7B")

# 如果模型不存在, 下载模型
if not os.path.exists(args.model):
    debug_logger.info(f'开始下载大模型：{model_id}')
    cache_dir = snapshot_download(model_id=model_id)
    output = subprocess.check_output(['ln', '-s', cache_dir, args.model], text=True)
    debug_logger.info(f'模型下载完毕！cache地址：{cache_dir}, 软链接地址：{args.model}')
else:
    debug_logger.info(f'{args.model}路径已存在，不再重复下载大模型（如果下载出错可手动删除此目录）')

debug_logger.info(f"CUDA_DEVICE: {model_config.CUDA_DEVICE}")


WorkerManager.THRESHOLD = 6000

app = Sanic("QAnything")
# 设置请求体最大为 400MB
app.config.REQUEST_MAX_SIZE = 400 * 1024 * 1024


# 将 /static 路径映射到 static 文件夹
app.static('/static', './static')

# 启动Milvus Lite服务
# @app.main_process_start
# async def start_dependent_services(app, loop):
#     debug_logger.info(f"default_server: {default_server.running}")
#     if not default_server.running:
#         start = time.time() 
#         default_server.set_base_dir(MILVUS_LITE_LOCATION)
#         default_server.start()
#         print(f"Milvus Lite started at {default_server.listen_port}", flush=True)
#         debug_logger.info(f"Milvus Lite started at {default_server.listen_port} in {time.time() - start} seconds.")


# # 关闭依赖的服务
# @app.main_process_stop
# async def end_dependent_services(app, loop):
#     if default_server.running:
#         default_server.stop()


# CORS中间件，用于在每个响应中添加必要的头信息
@app.middleware("response")
async def add_cors_headers(request, response):
    # response.headers["Access-Control-Allow-Origin"] = "http://10.234.10.144:5052"
    response.headers["Access-Control-Allow-Origin"] = "*"
    response.headers["Access-Control-Allow-Methods"] = "GET, POST, PUT, DELETE, OPTIONS"
    response.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization"
    response.headers["Access-Control-Allow-Credentials"] = "true"  # 如果需要的话


@app.middleware("request")
async def handle_options_request(request):
    if request.method == "OPTIONS":
        headers = {
            # "Access-Control-Allow-Origin": "http://10.234.10.144:5052",
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Methods": "GET, POST, PUT, DELETE, OPTIONS",
            "Access-Control-Allow-Headers": "Content-Type, Authorization",
            "Access-Control-Allow-Credentials": "true"  # 如果需要的话
        }
        return sanic_response.text("", headers=headers)

@app.before_server_start
async def init_local_doc_qa(app, loop):
    start = time.time()
    local_doc_qa = LocalDocQA()
    local_doc_qa.init_cfg(mode='local', args=args)
    debug_logger.info(f"LocalDocQA started in {time.time() - start} seconds.")
    app.ctx.local_doc_qa = local_doc_qa


# @app.after_server_stop
# async def close_milvus_lite(app, loop):
#     if default_server.running:
#         default_server.stop()


app.add_route(document, "/api/docs", methods=['GET'])
app.add_route(new_knowledge_base, "/api/local_doc_qa/new_knowledge_base", methods=['POST'])  # tags=["新建知识库"]
app.add_route(upload_weblink, "/api/local_doc_qa/upload_weblink", methods=['POST'])  # tags=["上传网页链接"]
app.add_route(upload_files, "/api/local_doc_qa/upload_files", methods=['POST'])  # tags=["上传文件"] 
app.add_route(local_doc_chat, "/api/local_doc_qa/local_doc_chat", methods=['POST'])  # tags=["问答接口"] 
app.add_route(list_kbs, "/api/local_doc_qa/list_knowledge_base", methods=['POST'])  # tags=["知识库列表"] 
app.add_route(list_docs, "/api/local_doc_qa/list_files", methods=['POST'])  # tags=["文件列表"]
app.add_route(get_total_status, "/api/local_doc_qa/get_total_status", methods=['POST'])  # tags=["获取所有知识库状态"]
app.add_route(clean_files_by_status, "/api/local_doc_qa/clean_files_by_status", methods=['POST'])  # tags=["清理数据库"]
app.add_route(delete_docs, "/api/local_doc_qa/delete_files", methods=['POST'])  # tags=["删除文件"] 
app.add_route(delete_knowledge_base, "/api/local_doc_qa/delete_knowledge_base", methods=['POST'])  # tags=["删除知识库"] 
app.add_route(rename_knowledge_base, "/api/local_doc_qa/rename_knowledge_base", methods=['POST'])  # tags=["重命名知识库"] 

@app.route('/stop', methods=['GET'])
async def stop(request):
    if default_server.running:
        default_server.stop()
    request.app.stop()
    return sanic_response.text("Server is stopping.")

class LocalDocQAServer:
    def __init__(self, host='0.0.0.0', port=8777):
        self.host = host
        self.port = port

    def start(self):
        app.run(host=self.host, port=self.port, single_process=True, access_log=False)

    def stop(self):
        res = requests.get('http://{self.host}:{self.port}/stop'.format(self.host, self.port))
        debug_logger.info(f"Stop qanything server: {res.text}")


def main():
    # default_server.set_base_dir(MILVUS_LITE_LOCATION)
    start = time.time() 
    # with default_server:
    if True:
        debug_logger.info(f"Milvus Lite started at {default_server.listen_port} in {time.time() - start} seconds.")

        # 根据命令行参数启动服务器
        qanything_server = LocalDocQAServer(host=args.host, port=args.port)

        signal.signal(signal.SIGINT, lambda sig, frame: qanything_server.stop())
        signal.signal(signal.SIGTERM, lambda sig, frame: qanything_server.stop())

        try:
            qanything_server.start()
        except TimeoutError:
            print('Wait for qanything server started timeout.')
        except RuntimeError:
            print('QAnything server already stopped.')


if __name__ == "__main__":
    main()


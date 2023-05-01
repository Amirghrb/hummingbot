import asyncio
import hashlib
import hmac
import json
import time
from collections import OrderedDict
from typing import Any, Dict
from urllib.parse import urlencode, urlsplit

from hummingbot.connector.time_synchronizer import TimeSynchronizer
from hummingbot.core.web_assistant.auth import AuthBase
from hummingbot.core.web_assistant.connections.data_types import RESTMethod, RESTRequest, WSRequest


class XtAuth(AuthBase):
    def __init__(self, api_key: str, secret_key: str, time_provider: TimeSynchronizer,hashAlgo="HmacSHA256",timeWindow="30000"):
        self.api_key = api_key
        self.secret_key = secret_key
        self.time_provider = time_provider
        self.hash=hashAlgo
        self.timeWindow=timeWindow
    async def rest_authenticate(self, request: RESTRequest) -> RESTRequest:
        #xt api dosn't need auth paratmer in it's parameters or data  
        """
        Adds the server time and the signature to the request, required for authenticated interactions. It also adds
        the required parameter in the request header.
        :param request: the request to be configured for authenticated interaction
        
        if request.method == RESTMethod.POST:
            request.data = self.add_auth_to_params(params=json.loads(request))
        else:
            request.params = self.add_auth_to_params(request)
"""
        headers = {}
        url=request.url.split("/")
        request.endpoint_url="/"+"/".join(url[3:5])
        if request.headers is not None:
            headers.update(request.headers)
            #add header for auth
        headers.update(self.header_for_authentication(request))
        request.headers = headers

        return request



    async def ws_authenticate(self, request: WSRequest) -> WSRequest:
        
        """
        This method is intended to configure a websocket request to be authenticated. Xt does not use this
        functionality
        """
        return request  # pass-through
    



#not useed, params dosen't need signature
    '''
    def add_auth_to_params(self,request:RESTRequest):
        timestamp = int(self.time_provider.time() * 1e3)

        request_params = OrderedDict(request.params or {})
        request_params["xt-validate-timestamp"] = timestamp

        signature = self._generate_signature(request,timestamp)
        request_params["xt-validate-signature"] = signature

        return request_params
    '''
    def header_for_authentication(self,request:RESTRequest) -> Dict[str, str]:
        timestamp = int(time.time()*1000)
        if request.is_auth_required:
            signature = self._generate_signature(request,timestamp)
            headers = {
                    'xt-validate-algorithms': self.hash,
                    'xt-validate-appkey': self.api_key,
                    'xt-validate-recvwindow': self.timeWindow,
                    "xt-validate-timestamp":str(timestamp),
                    "xt-validate-signature":signature
                    }
        else:
             headers = {
                    'xt-validate-algorithms': self.hash,
                    'xt-validate-appkey': self.api_key,
                    'xt-validate-recvwindow': self.timeWindow,
                    "xt-validate-timestamp":str(timestamp),
                    }    
        return headers

    def _generate_signature(self,request:RESTRequest,timestamp) -> str:
        #gnerate first part of message
        xv='xt-validate-algorithms={}&xt-validate-appkey={}&xt-validate-recvwindow={}&xt-validate-timestamp={}'
        xv=xv.format(self.hash,self.api_key,self.timeWindow,timestamp)
       
        #gnerate second part of message in this format
        # yv =#method#path#query#body
        if request.params!=None:
            paramsstr=self.paramsList2string(request.params)
        else:
            paramsstr=None    
        if request.data!=None:
            datastr=str(request.data)
        else:
            datastr=None    
        params=[str(request.method),request.endpoint_url,paramsstr, datastr]    
        yv=self.generateYvalue(params=params)
        # self.logger(xv,"\n",yv,"\n")<- for test
        #generate original message
        original=xv+yv
        # print(xv,"\n",yv,"\n")#<- for test
        # print(original,"\n")#<- for test
        
        signature = hmac.new(bytes(self.secret_key , 'latin-1'), bytes(original , 'latin-1'), digestmod = hashlib.sha256).hexdigest().upper()

        return signature

    def generateYvalue(self,params:list)->str:
        y=''        
        for param in params:
            if param==None:
                continue
            else:
                y+=("#"+param)
        return y

        # format params in param1=val1,param2=val2
    def paramsList2string(self,params:Dict[str,Any])->str:
        params=params
        temp=''
        res=''
        for param in params:
            temp=',{}={}'.format(param,params[param])
            res+=temp
        return  res[1:]   

#infile test

# balance=RESTRequest(
#     method=RESTMethod.GET,
#     url="https://sapi.xt.com/v4/balances",
#     endpoint_url='/v4/balances',
#     is_auth_required=True,
#     # params={"chain":"trc20","currency":"usdt"},
#     # data={"symbol":"XT_USDT",
#     #       "side":"BUY",
#     #       "type":"LIMIT",
#     #       "timeInForce":"GTC",
#     #       "bizType":"SPOT",
#     #       "price":3,
#     #       "quantity":2
#     #       }
    
#     )
# auth=XtAuth(
#     api_key="e4078472-4359-4eda-85e7-5ada79be17d4",
#     secret_key="0fb85c3278a1afa288a028d49eab412643ca068c",
#     time_provider=TimeSynchronizer()
    
# )   

# balanceReq= asyncio.run(auth.rest_authenticate(request=balance))
# print(balance.headers)

import asyncio
import aiohttp

class RPCClient():
    def __init__(self, rpc_url):
        self.session = None
        self.rpc_url = rpc_url

    async def open(self):
        if self.session is None or self.session.closed:
            self.session = aiohttp.ClientSession()

    async def close(self):
        if self.session and not self.session.closed:
            await asyncio.sleep(2)
            await self.session.close()

    async def __aenter__(self):
        await self.open()
        return self

    async def __aexit__(self, exc_type, exc, tb):
        await self.close()
        
    async def rpc_call(self, method, params=[]):
        async with self.session.post(self.rpc_url, json={
            "jsonrpc":"2.0",
            "id": 1,
            "method": method,
            "params": params
        }) as resp:
            result = await resp.json()
            return result["result"]

    async def process_block(self, block_number):
        block_task = self.rpc_call( "eth_getBlockByNumber", [hex(block_number), True])
        receipt_task = self.rpc_call( "eth_getBlockReceipts", [hex(block_number)])
        result = await asyncio.gather(block_task, receipt_task)
        block = result[0]
        block["receipts"] = result[1]
        return block
    
    async def get_batch(self, start_block, end_block):
        tasks = [self.process_block( b) for b in range(start_block, end_block)]
        return asyncio.gather(*tasks)


from __future__ import annotations

import logging
import pickle

import numpy as np
import numpy.typing as npt
import torch
from ray import serve

from market_simulation.conf import C
from market_simulation.models.order_model import OrderModel


@serve.deployment(
    num_replicas=1,
    ray_actor_options={"num_gpus": C.model_serving.num_gpus, "num_cpus": C.model_serving.num_cpus},
)
class OrderModelServing:
    """Serving Model with ray serve."""

    def __init__(self) -> None:
        self.model: OrderModel = self._load_model().cuda().eval()
        self.temperature = C.model_serving.temperature
        logging.info(f"Order model initialized, with temperature: {self.temperature}.")

    def _load_model(self) -> OrderModel:
        repo_id = C.model_serving.repo_id
        order_model = OrderModel.from_pretrained(C.model_serving.repo_id)
        logging.info(f"Loaded model from {repo_id}.")
        logging.info(f"Model configs: {order_model.num_layers}, {order_model.emb_dim}, {order_model.num_heads}")
        if C.model_serving.fp16:
            order_model.half()
            logging.info("Model converted to half precision.")
        return order_model

    @serve.batch(max_batch_size=C.model_serving.max_batch_size)  # type: ignore
    async def batch_inference(self, requests: list[npt.NDArray[np.int32]]) -> list[npt.NDArray[np.int32]]:
        """Batch inference."""
        batch_size = len(requests)
        input_tensor = torch.from_numpy(np.asarray(requests)).cuda()
        input_tensor = input_tensor.reshape((batch_size, C.order_model.seq_len, C.order_model.token_dim))
        logging.info(f"batch size: {batch_size}, input shape: {input_tensor.shape}")
        with torch.no_grad():
            output_tensor: np.ndarray = self.model.sample(input_tensor, self.temperature).int().cpu().reshape((batch_size, -1)).numpy()
        logging.info(f"output shape: {output_tensor.shape}")

        results: list[npt.NDArray[np.int32]] = []
        for i in range(batch_size):
            output = output_tensor[i]
            arr = np.array([output], dtype=np.int32)
            results.append(pickle.dumps(arr))  # type: ignore
        return results

    async def __call__(self, request) -> list[npt.NDArray[np.int32]]:  # noqa: ANN001
        """Handle request."""
        request_bytes = await request.body()
        arr = pickle.loads(request_bytes)
        return await self.batch_inference(arr)  # type: ignore


order_model_app = OrderModelServing.bind()  # type: ignore

import json
from typing import Dict

from pydantic import BaseModel
import redis

from app.controllers.manager.base_manager import TaskManager
from app.models.schema import AudioRequest, SubtitleRequest, TaskVideoRequest, VideoParams
from app.services import task as tm

FUNC_MAP = {
    "start": tm.start,
    # 'start_test': tm.start_test
}

PARAM_MODEL_MAP = {
    "VideoParams": VideoParams,
    "TaskVideoRequest": TaskVideoRequest,
    "SubtitleRequest": SubtitleRequest,
    "AudioRequest": AudioRequest,
}

STOP_AT_MODEL_MAP = {
    "video": TaskVideoRequest,
    "subtitle": SubtitleRequest,
    "audio": AudioRequest,
}


class RedisTaskManager(TaskManager):
    def __init__(
        self,
        max_concurrent_tasks: int,
        redis_url: str,
        max_queued_tasks: int = 100,
    ):
        self.redis_client = redis.Redis.from_url(redis_url)
        super().__init__(max_concurrent_tasks, max_queued_tasks=max_queued_tasks)

    def create_queue(self):
        return "task_queue"

    @staticmethod
    def _serialize_kwargs(kwargs: Dict) -> Dict:
        serializable_kwargs = kwargs.copy()
        params = serializable_kwargs.get("params")
        if isinstance(params, BaseModel):
            serializable_kwargs["params"] = params.model_dump(mode="json")
            serializable_kwargs["_params_model"] = params.__class__.__name__
        return serializable_kwargs

    @staticmethod
    def _restore_kwargs(kwargs: Dict) -> Dict:
        restored_kwargs = kwargs.copy()
        params_model_name = restored_kwargs.pop("_params_model", "")
        params = restored_kwargs.get("params")
        if isinstance(params, dict):
            model_cls = PARAM_MODEL_MAP.get(params_model_name)
            if model_cls is None:
                model_cls = STOP_AT_MODEL_MAP.get(restored_kwargs.get("stop_at"), VideoParams)
            restored_kwargs["params"] = model_cls(**params)
        return restored_kwargs

    def enqueue(self, task: Dict):
        task_with_serializable_params = task.copy()
        task_with_serializable_params["kwargs"] = self._serialize_kwargs(
            task.get("kwargs", {})
        )

        # 将函数对象转换为其名称
        task_with_serializable_params["func"] = task["func"].__name__
        self.redis_client.rpush(self.queue, json.dumps(task_with_serializable_params))

    def dequeue(self):
        task_json = self.redis_client.lpop(self.queue)
        if task_json:
            task_info = json.loads(task_json)
            # 将函数名称转换回函数对象
            task_info["func"] = FUNC_MAP[task_info["func"]]

            task_info["kwargs"] = self._restore_kwargs(task_info.get("kwargs", {}))

            return task_info
        return None

    def is_queue_empty(self):
        return self.redis_client.llen(self.queue) == 0

    def queue_size(self):
        return self.redis_client.llen(self.queue)

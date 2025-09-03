"""A wrapper for the Gemini API."""

import inspect
import time
from typing import Any

from google import genai

from llms.llm_wrapper import LlmWrapper
from models.config import VibeCheckConfig
from models.exceptions import VibeResponseTypeException
from utils.logger import console_logger


class GeminiWrapper(LlmWrapper):
    """A wrapper for the Gemini API."""

    def __init__(self, client: genai.Client, model: str, config: VibeCheckConfig):
        """
        Initialize the Gemini wrapper.

        Args:
            client: The Gemini client.
            model: The model to use.
            config: VibeCheckConfig containing runtime knobs (e.g., num_tries).

        """
        self.client = client
        self.model = model
        self.config = config

    def vibe_eval_statement(self, statement: str) -> bool:
        """
        Evaluate a statement and returns a boolean.

        Args:
            statement: The statement to evaluate.

        Returns:
            A boolean indicating whether the statement is true or false.

        Raises:
            VibeResponseTypeException: If the API is unable to provide a valid response.

        """
        # determine total tries using new/back-compat knobs
        total_tries = max(self.config.num_tries, self.config.max_retries + 1)
        for attempt in range(1, total_tries + 1):
            # catch any error thrown in this loop, log at debug, and retry
            try:
                console_logger.debug(f"[Attempt {attempt}/{total_tries}] {statement}")
                t0 = time.perf_counter()
                response = self.client.models.generate_content(
                    model=self.model,
                    contents=statement,
                    config=genai.types.GenerateContentConfig(system_instruction=self._eval_statement_instruction),
                )
                dt = time.perf_counter() - t0
            except Exception as e:
                console_logger.debug(f"API error on attempt {attempt}: {e}")
                if attempt < total_tries:
                    delay = min(self.config.backoff_base * (2 ** (attempt - 1)), self.config.backoff_max)
                    console_logger.debug(f"Backing off for {delay:.2f}s before retry (API error)")
                    time.sleep(delay)
                continue

            output_text = (getattr(response, "text", None) or "").lower().strip()
            console_logger.debug(f"Response ({dt*1000:.0f} ms): {output_text!r}")

            if "true" in output_text:
                return True
            if "false" in output_text:
                return False
            if attempt < total_tries:
                delay = min(self.config.backoff_base * (2 ** (attempt - 1)), self.config.backoff_max)
                console_logger.debug(f"Backing off for {delay:.2f}s before retry (no boolean found)")
                time.sleep(delay)

        raise VibeResponseTypeException("Unable to get a valid response from the Gemini API.")

    def vibe_call_function(self, func_signature: inspect.signature, docstring: str, *args, **kwargs) -> Any:
        """
        Call a function and return the LLM-evaluated result.

        Builds a structured prompt from the provided signature, docstring, and arguments,
        queries Gemini, and optionally enforces the output's Python type.

        Args:
            func_signature (inspect.signature): The function signature being invoked.
            docstring (str): The function's docstring used to give additional context to the model.
            *args: Positional arguments to include in the call.
            **kwargs: Keyword arguments to include in the call.

        Returns:
            Any: If return type is not found in the function signature, defaults to str.
            Otherwise, returns the value coerced to the return type on success.

        Raises:
            VibeResponseTypeException: If the model fails to produce a valid response matching
                the return type (when specified) within the configured number of tries.

        """
        if func_signature.return_annotation is inspect.Signature.empty:
            return_type = None
        else:
            return_type = func_signature.return_annotation
        return_type_line = f"\nReturn Type: {return_type}" if return_type else ""
        prompt = f"""
        Function Signature: {func_signature}
        Docstring: {docstring}
        Arguments: {args}, {kwargs}{return_type_line}
        """.strip()

        # determine total tries using new/back-compat knobs
        total_tries = max(self.config.num_tries, self.config.max_retries + 1)
        for attempt in range(1, total_tries + 1):
            # catch any error thrown in this loop, log at debug, and retry
            try:
                console_logger.debug(f"[Attempt {attempt}/{total_tries}] Function call prompt: {prompt}")
                t0 = time.perf_counter()
                response = self.client.models.generate_content(
                    model=self.model,
                    contents=prompt,
                    config=genai.types.GenerateContentConfig(system_instruction=self._call_function_instruction),
                )
                dt = time.perf_counter() - t0
            except Exception as e:
                console_logger.debug(f"API error on attempt {attempt}: {e}")
                if attempt < total_tries:
                    delay = min(self.config.backoff_base * (2 ** (attempt - 1)), self.config.backoff_max)
                    console_logger.debug(f"Backing off for {delay:.2f}s before retry (API error)")
                    time.sleep(delay)
                continue

            raw_text = (getattr(response, "text", None) or "").strip()
            console_logger.debug(f"Function call raw response ({dt*1000:.0f} ms): {raw_text!r}")

            # if no return type was specified, default to string
            if return_type is None:
                return raw_text

            # otherwise, enforce the type with shared helpers.
            value = self._maybe_coerce(raw_text, return_type)
            if self._is_match(value, return_type):
                return value

            console_logger.debug("Response did not match expected type; retrying...")
            if attempt < total_tries:
                delay = min(self.config.backoff_base * (2 ** (attempt - 1)), self.config.backoff_max)
                console_logger.debug(f"Backing off for {delay:.2f}s before retry (type mismatch)")
                time.sleep(delay)

        raise VibeResponseTypeException(f"Unable to get a valid response matching {return_type!r} from the Gemini API.")

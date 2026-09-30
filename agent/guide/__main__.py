"""`python -m guide [问题…]` —— 命令行里把一趟导览打出来看。

★包自己不能被执行（`python -m guide` 找的是 `__main__`），所以那一段自看代码
  放在 `__init__.py` 的 `if __name__` 里是够不着的。这里把它接上：
  两处只有一个入口，避免"能跑的那份"和"判据读的那份"不是同一段。
"""

from . import _main

if __name__ == '__main__':
    _main()

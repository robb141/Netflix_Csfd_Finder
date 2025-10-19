# import requests
# from bs4 import BeautifulSoup
#
# req = requests.get('http://quotes.toscrape.com/').text
# soup = BeautifulSoup(req, 'html.parser')
# # print(soup)
#
# print(soup.find(class_='col-md-8'))
#
#
#


# Sample input
# [1, 11, 3, 0, 15, 5, 2, 4, 10, 7, 12, 6]
# sample output: [0,7]



def print_board(bo):
    for i in range(len(bo)):
        if i % 3 == 0 and i != 0:
            print("- - - - - - - - - - - - - ")

        for j in range(len(bo[0])):
            if j % 3 == 0 and j != 0:
                print(" | ", end="")

            if j == 8:
                print(bo[i][j])
            else:
                print(str(bo[i][j]) + " ", end="")




def possible(a, b, x):
    global board
    len_board = len(board)
    if x in board[a]:
        return False
    if any(x == board[i][b] for i in range(len_board)):
        return False
    row = a // 3
    col = b // 3
    directions = [-2, -1, 1, 2]
    for i in directions:
        for j in directions:
            if 0 <= a + i < len_board and (a + i) // 3 == row and 0 <= b + j < len_board and (b + j) // 3 == col:
                if x == board[a+i][b+j]:
                    return False
    return True
# board = [
#     [7,8,0,4,0,0,1,2,0],
#     [6,0,0,0,7,5,0,0,9],
#     [0,0,0,6,0,1,0,7,8],
#     [0,0,7,0,4,0,2,6,0],
#     [0,0,1,0,5,0,9,3,0],
#     [9,0,4,0,6,0,0,0,5],
#     [0,7,0,3,0,0,0,1,2],
#     [1,2,0,0,0,7,4,0,0],
#     [0,4,9,2,0,6,0,0,7]
# ]

board = [
    [0,6,5,2,0,0,4,0,0],
    [0,7,0,0,0,0,1,0,0],
    [0,2,0,4,0,0,0,0,8],
    [0,0,0,0,0,0,0,1,0],
    [0,0,0,5,9,0,6,0,7],
    [0,0,0,3,0,0,0,5,0],
    [0,0,0,0,0,7,0,0,0],
    [5,0,1,0,0,0,0,2,0],
    [0,4,3,0,0,0,0,0,0],
]

# print(possible(1,1,8))


def solve():
    global board
    for i in range(len(board)):
        for j in range(len(board[i])):
            if board[i][j] == 0:
                for x in range(1, 10):
                    if possible(i, j, x):
                        board[i][j] = x
                        solve()
                        board[i][j] = 0
                return
    print_board(board)

# def is_valid(x, r, c):
#     if x <= 9 and board.count(x)
#

solve()
# print_board(board)


# inp = [7,8,11,4,15,0,1,2,6]
# # target = 15
# #
# # d = {i: 0 for i in inp}
# # print(d)
# # result = []
# # for i in inp:
# #     if d[i] == 0 and target - i in d.keys():
# #         d[target - i] = 1
# #         d[i] = 1
# #         result.append((i, target - i))
# #
# # print(result)



# def first_non_repeating_character(string):
#     d = {}
#     for st in string:
#         if st not in d.keys():
#             total = string.count(st)
#             if total == 1:
#                 return st
#             d[st] = total
#     return '_'
#
#
# print(first_non_repeating_character('abcabcabc'))


def first_non_repeating(nums):
    # return {x: nums.count(x) for x in nums}
    d = {x: 0 for x in nums}
    for num in nums:
        if d[num] == 1:
            return num
        d[num] += 1
    return -1

# print(first_non_repeating([2,1,3,5,3,2]))


# a = [1,2,3,4,5,6,7]
# for num in range(len(a)//2):
#     a[num], a[-num-1] = a[-num-1], a[num]
#     print(a)


# b = [[1,2,3], [4,5,6], [7,8,9]]
#
#
# def rotate_90(img):
#     # zle, lebo vracia nove pole
#     c = [[] for _ in range(len(img))]
#     for i in range(len(img)-1, -1, -1):
#         for j in range(len(img)):
#             c[j].append(img[i][j])
#     return c
#
#
# for x in b:
#     print(x)
#
#
# print('\n')
# for x in rotate_90(b):
#     print(x)
#
#
# c = [[1,2,3], [4,5,6], [7,8,9]]
# print('\n')
# for i in range(len(c)):
#     for j in range(len(c[i])):
#         if i < j:
#             c[i][j], c[j][i] = c[j][i], c[i][j]
# for num in range(len(c)):
#     for nnn in range(len(c[num])//2):
#         c[num][nnn], c[num][-nnn-1] = c[num][-nnn-1], c[num][nnn]
# print(c)


# r = [0, 0, -5, 30212]
# t = [-5, -10, -4 , 9]
# vektor = -8
#
#
# def sum_to_v(a, b, v):
#     # for elem in a:
#     #     if v - elem in b:
#     #         return True
#     # return False
#     return any(x+y==v for x in a for y in b)
#
#
# print(sum_to_v(r, t, vektor))


def findLongestSubarrayBySum(nums, s):
    max_num = 0
    res = []
    for i in range(len(nums)):
        total = nums[i]
        j = i + 1
        while total <= s and j < len(nums):
            if total == s and max_num < j - i:
                max_num = j - i
                res = [i + 1, j]
            total += nums[j]
            j += 1
    if res:
        return res
    return -1


# print(findLongestSubarrayBySum([1,2,3,4,5,0,0,0,6,7,8,9], 15))


def find_longest(nums, x):
    right, left = 1, 0
    res = [0, 0]
    total = nums[left] + nums[right]
    while right < len(nums):
        if total == x and res[1] - res[0] < right - left:
            res = [left+1, right+1]
            right += 1
            if right < len(nums):
                total += nums[right]
        elif total > x:
            total -= nums[left]
            left += 1
        else:
            right += 1
            if right < len(nums):
                total += nums[right]
    return res


# print(find_longest([1,2,3,4,5,0,0,0,6,7,8,9], 15))


# class Solution(object):
#     def findLongestWord(self, s, d):
#         """
#         :type s: str
#         :type d: List[str]
#         :rtype: str
#         """
#         longest = ''
#         for elem in d:
#             if len(elem) > len(longest):
#                 e = 0
#                 start = 0
#                 flag = True
#                 while e < len(elem):
#                     if elem[e] in s[start:]:
#                         # flag = True
#                         start = s[start:].index(elem[e]) + 1
#                         e += 1
#                     else:
#                         flag = False
#                         break
#                 if flag:
#                     longest = elem
#         return longest

# Input:
# s = "abpcplea", d = ["ale","apple","monkey","plea"]
#
# Output:
# "apple"


# sol = Solution()
# print(sol.findLongestWord(s = "abpcplea", d = ["a","b","c"]))


# class Solution(object):
#     def coinChange(self, coins, amount):
#         """
#         :type coins: List[int]
#         :type amount: int
#         :rtype: int
#         """
#         # A Python program to print all
#         # permutations using library function
#         from itertools import permutations
#
#         # Get all permutations of [1, 2, 3]
#         perm = permutations(coins)
#
#         result = -1
#         # Print the obtained permutations
#         for i in list(perm):
#             temp = 0
#             rest = amount
#             for j in i:
#                 temp += rest // j
#                 rest = rest % j
#                 if rest == 0 and (result == -1 or temp < result):
#                     result = temp
#         return result


# class Solution(object):
#     def coinChange(self, coins, amount):
#         dp = [0] + [float('inf')] * amount
#
#         for coin in coins:
#             for i in range(coin, amount + 1):
#                 dp[i] = min(dp[i], dp[i - coin] + 1)
#
#         return dp[-1] if dp[-1] != float('inf') else -1
#
# sol = Solution()
# print(sol.coinChange(coins = [186,419,83,408], amount = 6249))

# # Any Coins and Amounts:
# def _change_matrix(coin_set, change_amount):
#     matrix = [[0 for m in range(change_amount + 1)] for m in range(len(coin_set) + 1)]
#     for i in range(change_amount + 1):
#         matrix[0][i] = i
#     return matrix
#
# def change_making(coins, change):
#     matrix = _change_matrix(coins, change)
#     for c in range(1, len(coins) + 1):
#         for r in range(1, change + 1):
#
#             if coins[c-1] == r:
#                 matrix[c][r] = 1
#
#             elif coins[c-1] > r: #("angle brackets aren't allowed in YT description") r:
#                 matrix[c][r] = matrix[c-1][r]
#
#             else:
#                 matrix[c][r] = min(matrix[c - 1][r], 1 + matrix[c][r - coins[c - 1]])
#
#     return matrix[-1][-1]
#
#
# print(change_making([1,10,25], 32 ))


def fib(n):
    if n == 1 or n == 2:
        return 1
    a = 1
    b = 1
    for _ in range(3, n+1):
        temp = b
        b = a + temp
        a = temp
    return b


# print(fib(100000))

